#!/usr/bin/env python3
"""
Spotify Playlist -> MP3 — Hybrid Smart Search Pro Edition

What you get in this single script:
- Pro core: async/threaded downloads, structured logging, Rich/tqdm progress, tagging + cover art, .spotifydlrc config
- Optional `--smart-search`: hybrid YouTube matching (YouTube Data API v3 + yt-dlp fallback)
- Reads Spotify creds and optional `youtube_api_key` from `credentials.json` or env vars

credentials.json example:
{
  "client_id": "your_spotify_client_id",
  "client_secret": "your_spotify_client_secret",
  "redirect_uri": "http://localhost:8888/callback",
  "youtube_api_key": "YOUR_YT_DATA_API_KEY"
}

Quick start:
  python spotify_playlist_to_mp3_hybrid.py <playlist_url> -w 6 --async --smart-search -o downloads

Notes:
- On Windows, set --ffmpeg explicitly if not on PATH.
- If YouTube API key is absent, smart search falls back to yt-dlp metadata ranking.
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import signal
import logging
import argparse
import concurrent.futures
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
import configparser

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from yt_dlp import YoutubeDL
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC

# ---------- Optional libs ----------
try:  # progress UI
    from rich.progress import Progress, BarColumn, TimeRemainingColumn, MofNCompleteColumn, TextColumn
    from rich.console import Console
    _RICH = True
except Exception:
    from tqdm import tqdm  # type: ignore
    _RICH = False

try:  # async optional
    import aiohttp  # type: ignore
    import asyncio
    _ASYNC_OK = True
except Exception:
    aiohttp = None
    asyncio = None
    _ASYNC_OK = False

try:
    from pathvalidate import sanitize_filename as _sanitize_filename  # type: ignore
except Exception:
    _sanitize_filename = None

try:
    from tenacity import retry as _tenacity_retry, stop_after_attempt, wait_exponential  # type: ignore
except Exception:
    _tenacity_retry = None

try:
    from pythonjsonlogger import jsonlogger  # type: ignore
    _JSON_LOG = True
except Exception:
    jsonlogger = None
    _JSON_LOG = False

try:
    from ratelimit import limits, sleep_and_retry  # type: ignore
    _RATE_LIMIT = True
except Exception:
    limits = None
    sleep_and_retry = None
    _RATE_LIMIT = False

# ---------- Retry decorator (fallback) ----------
def simple_retry(max_attempts: int = 3, base_wait: float = 1.0, max_wait: float = 10.0):
    def decorator(fn: Callable):
        def wrapper(*args, **kwargs):
            attempt = 0
            delay = base_wait
            while True:
                try:
                    return fn(*args, **kwargs)
                except KeyboardInterrupt:
                    raise
                except Exception:  # noqa: BLE001
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    time.sleep(min(delay, max_wait))
                    delay *= 2
        return wrapper
    return decorator

if _tenacity_retry:
    def retryable(fn=None, *, attempts=3):
        if fn is None:
            return lambda f: _tenacity_retry(stop=stop_after_attempt(attempts),
                                             wait=wait_exponential(multiplier=1, min=1, max=10))(f)
        return _tenacity_retry(stop=stop_after_attempt(attempts),
                               wait=wait_exponential(multiplier=1, min=1, max=10))(fn)
else:
    def retryable(fn=None, *, attempts=3):
        if fn is None:
            return lambda f: simple_retry(max_attempts=attempts)(f)
        return simple_retry(max_attempts=attempts)(fn)

# ---------- Filename sanitization ----------
INVALID_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def safe_filename(name: str, maxlen: int = 120) -> str:
    name = (name or "").strip()
    if _sanitize_filename:
        cleaned = _sanitize_filename(name)
    else:
        cleaned = INVALID_FN.sub("", name)
        cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > maxlen:
        cut = cleaned[:maxlen]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        cleaned = cut
    return cleaned or "untitled"

# ---------- Data structures ----------
@dataclass
class Track:
    id: str
    title: str
    artists: List[str]
    album: Optional[str]
    duration_ms: int
    cover_url: Optional[str]
    release_year: Optional[str] = None
    total_tracks: Optional[int] = None
    genres: List[str] = None  # populated later via artist lookup

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists)

# ---------- Config ----------

def load_config(cli_args: argparse.Namespace) -> argparse.Namespace:
    config = configparser.ConfigParser()
    paths: List[Path] = []
    home = Path.home() / ".spotifydlrc"
    cwd = Path.cwd() / ".spotifydlrc"
    if home.exists():
        paths.append(home)
    if cwd.exists():
        paths.append(cwd)
    if paths:
        config.read([str(p) for p in paths], encoding="utf-8")
        if config.has_section("defaults"):
            d = config["defaults"]
            cli_args.out = cli_args.out or d.get("out", cli_args.out)
            cli_args.bitrate = cli_args.bitrate or d.getint("bitrate", cli_args.bitrate)
            cli_args.workers = cli_args.workers or d.getint("workers", cli_args.workers)
            if not cli_args.skip_existing:
                cli_args.skip_existing = d.getboolean("skip_existing", False)
            cli_args.ffmpeg = cli_args.ffmpeg or d.get("ffmpeg_path", cli_args.ffmpeg)
            if not cli_args.verbose:
                cli_args.verbose = d.getboolean("verbose", False)
            if not getattr(cli_args, "use_async", False):
                setattr(cli_args, "use_async", d.getboolean("async", False))
            cli_args.log_file = cli_args.log_file or d.get("log_file", cli_args.log_file)
            cli_args.youtube_api_key = getattr(cli_args, 'youtube_api_key', None) or d.get("youtube_api_key", None)
    return cli_args

# ---------- Logging ----------
from logging.handlers import RotatingFileHandler

class RedactingFormatter(logging.Formatter):
    _key_re = re.compile(r"(client_(?:id|secret)|youtube_api_key)\s*[:=]\s*([^\s,'\"]+)", re.IGNORECASE)
    _token_re = re.compile(r"[A-Za-z0-9_-]{24,}")
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        msg = self._key_re.sub(r"\1=***REDACTED***", msg)
        msg = self._token_re.sub(lambda m: "***REDACTED***" if len(m.group(0)) >= 32 else m.group(0), msg)
        return msg

def configure_logging(verbose: bool, log_file: Optional[str]) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    # clear handlers
    for h in list(root.handlers):
        root.removeHandler(h)

    # choose formatter
    if _JSON_LOG and jsonlogger:
        base_formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(message)s')
    else:
        base_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    formatter = RedactingFormatter(base_formatter._fmt)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file:
        fh = RotatingFileHandler(log_file, maxBytes=2*1024*1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

# ---------- Credentials ----------
CRED_JSON = Path("credentials.json")

def load_credentials() -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    env_id = os.getenv("SPOTIFY_CLIENT_ID")
    env_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    env_redirect = os.getenv("SPOTIFY_REDIRECT_URI")
    env_yt = os.getenv("YOUTUBE_API_KEY")
    if env_id and env_secret:
        return env_id, env_secret, env_redirect, env_yt
    if CRED_JSON.exists():
        try:
            data = json.loads(CRED_JSON.read_text("utf-8"))
            return data.get("client_id"), data.get("client_secret"), data.get("redirect_uri"), data.get("youtube_api_key")
        except Exception as e:  # noqa: BLE001
            logging.error("Failed reading credentials.json: %s", e)
    logging.warning("No credentials.json found and no SPOTIFY_* env vars set.")
    return None, None, None, env_yt


def get_spotify_client() -> spotipy.Spotify:
    client_id, client_secret, _redir, _yt = load_credentials()
    if not client_id or not client_secret:
        logging.error("Spotify credentials missing. Provide credentials.json or env vars.")
        sys.exit(1)
    creds = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    return spotipy.Spotify(client_credentials_manager=creds)


def parse_playlist_id(url_or_id: str) -> str:
    s = url_or_id.strip()
    if "playlist" in s:
        m = re.search(r"playlist[/:]([A-Za-z0-9]+)", s)
        if m:
            return m.group(1)
    if s.startswith("spotify:playlist:"):
        return s.split(":")[-1]
    return s

# Rate-limited decorator for Spotify calls
if _RATE_LIMIT:
    @sleep_and_retry
    @limits(calls=10, period=1)
    def _rl_pass():
        return True
else:
    def _rl_pass():
        return True

# ---------- Spotify fetching ----------
@dataclass
class Track:
    id: str
    title: str
    artists: List[str]
    album: Optional[str]
    duration_ms: int
    cover_url: Optional[str]
    release_year: Optional[str] = None
    total_tracks: Optional[int] = None
    genres: List[str] = None

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists)

@retryable(attempts=3)
def fetch_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> List[Track]:
    items: List[Track] = []
    fields = "items.track(id,name,artists(id,name),album(name,images,release_date,total_tracks),duration_ms),next"
    res = sp.playlist_items(playlist_id, fields=fields, limit=100)
    while res:
        _rl_pass()
        for it in res.get("items", []):
            t = it.get("track")
            if not t:
                continue
            artists = [a.get("name") for a in t.get("artists", []) if a]
            artist_ids = [a.get("id") for a in t.get("artists", []) if a and a.get("id")]
            album = t.get("album") or {}
            imgs = album.get("images", [])
            cover_url = imgs[0]["url"] if imgs else None
            release_date = album.get("release_date") or ""
            release_year = release_date.split("-")[0] if release_date else None
            total_tracks = album.get("total_tracks")
            tr = Track(
                id=t.get("id"),
                title=t.get("name"),
                artists=artists,
                album=album.get("name"),
                duration_ms=t.get("duration_ms") or 0,
                cover_url=cover_url,
                release_year=release_year,
                total_tracks=total_tracks,
                genres=[],
            )
            setattr(tr, "_artist_ids", artist_ids)
            items.append(tr)
        res = sp.next(res) if res.get("next") else None
    enrich_genres(sp, items)
    return items

@retryable(attempts=3)
def enrich_genres(sp: spotipy.Spotify, tracks: List[Track]) -> None:
    unique_ids: List[str] = []
    for tr in tracks:
        for aid in (getattr(tr, "_artist_ids", []) or []):
            if aid and aid not in unique_ids:
                unique_ids.append(aid)
    for i in range(0, len(unique_ids), 50):
        _rl_pass()
        batch = unique_ids[i:i+50]
        data = sp.artists(batch)
        id_to_genres = {a["id"]: a.get("genres", []) for a in data.get("artists", [])}
        for tr in tracks:
            gset = set(tr.genres or [])
            for aid in (getattr(tr, "_artist_ids", []) or []):
                gset.update(id_to_genres.get(aid, []))
            tr.genres = sorted(gset)

# ---------- Hybrid Smart Search ----------
class HybridSmartSearch:
    def __init__(self, api_key: Optional[str], verbose: bool = False):
        self.api_key = api_key
        self.verbose = verbose
        self.session = requests.Session()

    def _log(self, msg: str):
        if self.verbose:
            logging.info(f"[SmartSearch] {msg}")

    def _iso_to_seconds(self, duration: str) -> int:
        # Converts ISO 8601 duration (e.g., PT3M25S) to seconds
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
        if not m:
            return 0
        h = int(m.group(1) or 0)
        mm = int(m.group(2) or 0)
        ss = int(m.group(3) or 0)
        return h * 3600 + mm * 60 + ss

    def _score(self, title: str, channel: str, duration: int, target_title: str, artist: str, target_dur: int) -> int:
        t = title.lower()
        a = artist.lower()
        ch = channel.lower()
        s = 0
        # channel relevance
        if a and a in ch:
            s += 12
        if "vevo" in ch:
            s += 18
        if "official" in ch or "official" in t:
            s += 10
        # title token overlap
        tokens = set(re.findall(r"\w+", t))
        target_tokens = set(re.findall(r"\w+", (target_title + " " + a).lower()))
        s += min(len(tokens & target_tokens), 15) * 2
        # duration closeness
        if duration and target_dur:
            diff = abs(duration - target_dur)
            if diff <= 5:
                s += 30
            elif diff <= 10:
                s += 16
            else:
                s -= min(diff // 2, 20)
        # penalties
        if "live" in t and "live" not in target_title.lower():
            s -= 15
        if "cover" in t and "cover" not in target_title.lower():
            s -= 10
        if "remix" in t and "remix" not in target_title.lower():
            s -= 8
        return s

    def _api_candidates(self, query: str) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []
        search_url = (
            "https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet&type=video&maxResults=10&q={requests.utils.quote(query)}&key={self.api_key}"
        )
        r = self.session.get(search_url, timeout=10)
        if r.status_code != 200:
            self._log(f"YouTube API search failed: {r.status_code}")
            return []
        items = r.json().get("items", [])
        ids = ",".join(i.get("id", {}).get("videoId", "") for i in items if i.get("id"))
        if not ids:
            return []
        details_url = (
            "https://www.googleapis.com/youtube/v3/videos"
            f"?part=contentDetails,snippet&id={ids}&key={self.api_key}"
        )
        r2 = self.session.get(details_url, timeout=10)
        if r2.status_code != 200:
            self._log(f"YouTube API details failed: {r2.status_code}")
            return []
        videos = r2.json().get("items", [])
        out = []
        for v in videos:
            snippet = v.get("snippet", {})
            duration = self._iso_to_seconds(v.get("contentDetails", {}).get("duration", ""))
            out.append({
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "video_id": v.get("id"),
                "duration": duration,
            })
        return out

    def _yt_dlp_fallback(self, query: str, target_duration: int) -> Optional[str]:
        try:
            ydl_opts = {
                'quiet': True,
                'default_search': 'ytsearch10',
                'extract_flat': True,
                'noplaylist': True,
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
            entries = (info or {}).get('entries') or []
            best = None
            best_diff = 1e9
            for e in entries:
                dur = e.get('duration') or 0
                diff = abs(dur - target_duration) if target_duration else 9999
                if diff < best_diff:
                    best_diff = diff
                    best = e
            if best and best.get('url'):
                return best['url']
        except Exception as e:
            self._log(f"yt-dlp fallback failed: {e}")
        return None

    def search_url(self, query: str, title: str, artist: str, duration_ms: int) -> Optional[str]:
        duration_s = (duration_ms or 0) // 1000
        # Try API path
        try:
            cands = self._api_candidates(query)
        except Exception as e:
            self._log(f"API candidates error: {e}")
            cands = []
        if cands:
            scored = [(self._score(c['title'], c['channel'], c['duration'], title, artist, duration_s), c) for c in cands]
            scored.sort(key=lambda x: x[0], reverse=True)
            top_score, top = scored[0]
            self._log(f"Selected via API: {top['title']} | {top['channel']} | score={top_score}")
            if top_score >= 50:
                return f"https://www.youtube.com/watch?v={top['video_id']}"
        # Fallback to yt-dlp-only
        return self._yt_dlp_fallback(query, duration_s)

# ---------- Download + tagging ----------
class YTDLPWrapper:
    def __init__(self, ffmpeg_path: Optional[str], verbose: bool, smart: Optional[HybridSmartSearch] = None):
        self.ffmpeg_path = ffmpeg_path
        self.verbose = verbose
        self.smart = smart

    def _opts(self, outtmpl: str, bitrate_kbps: int, format_expr: Optional[str] = None) -> Dict[str, Any]:
        fmt = format_expr or 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best'
        opts: Dict[str, Any] = {
            'format': fmt,
            'outtmpl': outtmpl,
            'noplaylist': True,
            'quiet': not self.verbose,
            'no_warnings': True,
            'default_search': 'ytsearch1',
            'retries': 3,
            'ignoreerrors': True,
            'noprogress': True,
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': str(bitrate_kbps),
                },
                {'key': 'FFmpegMetadata'}
            ],
        }
        if self.ffmpeg_path:
            opts['ffmpeg_location'] = self.ffmpeg_path
        return opts

    def _download_url(self, url: str, out_path_template: str, bitrate_kbps: int) -> Optional[Path]:
        out_dir = Path(out_path_template).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        with YoutubeDL(self._opts(out_path_template, bitrate_kbps)) as ydl:
            ydl.extract_info(url, download=True)
        mp3s = sorted(out_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        return mp3s[0] if mp3s else None

    @retryable(attempts=2)
    def download_to_mp3(self, search_query: str, out_path_template: str, bitrate_kbps: int,
                        track: Optional[Track] = None, enable_smart: bool = False) -> Optional[Path]:
        # smart search tries to resolve a concrete URL first
        if enable_smart and self.smart and track is not None:
            url = self.smart.search_url(search_query, track.title, track.artist_str, track.duration_ms)
            if url:
                try:
                    return self._download_url(url, out_path_template, bitrate_kbps)
                except Exception:
                    pass  # fall back to normal flow below
        # legacy/basic: let yt-dlp search and pick bestaudio
        out_dir = Path(out_path_template).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        with YoutubeDL(self._opts(out_path_template, bitrate_kbps)) as ydl:
            info = ydl.extract_info(search_query, download=True)
            if isinstance(info, dict) and info.get('entries'):
                info = info['entries'][0]
        mp3s = sorted(out_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        return mp3s[0] if mp3s else None

class Tagger:
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    def embed_tags(self, mp3_path: Path, track: Track, index: int) -> None:
        try:
            try:
                audio = EasyID3(str(mp3_path))
            except Exception:
                audio = EasyID3()
                audio.save(str(mp3_path))
                audio = EasyID3(str(mp3_path))

            audio['title'] = track.title
            audio['artist'] = track.artist_str
            if track.album:
                audio['album'] = track.album
            audio['tracknumber'] = str(index)
            if track.release_year:
                audio['date'] = track.release_year
            if track.genres:
                audio['genre'] = ', '.join(track.genres[:3])
            if track.total_tracks:
                audio['tracktotal'] = str(track.total_tracks)
            audio.save()

            if track.cover_url:
                try:
                    r = self.session.get(track.cover_url, timeout=10)
                    if r.status_code == 200:
                        id3 = ID3(str(mp3_path))
                        id3.delall('APIC')
                        id3.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=r.content))
                        id3.save()
                except Exception:
                    pass
        except Exception:
            logging.debug("Tagging failed for %s", mp3_path)

# ---------- Async helper (cover art) ----------
async def fetch_cover_bytes_async(url: str, timeout: int = 10) -> Optional[bytes]:
    if not (_ASYNC_OK and aiohttp):
        return None
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        return None
    return None

# ---------- Core downloader ----------
class PlaylistDownloader:
    def __init__(self, sp: spotipy.Spotify, out_dir: Path, bitrate: int, workers: int,
                 skip_existing: bool, verbose: bool, ffmpeg_path: Optional[str], log_file: Optional[Path],
                 use_async: bool = False, smart_search: bool = False, youtube_api_key: Optional[str] = None):
        self.sp = sp
        self.out_dir = out_dir
        self.bitrate = bitrate
        self.workers = max(1, workers)
        self.skip_existing = skip_existing
        self.verbose = verbose
        self.ffmpeg_path = ffmpeg_path
        self.log_file = log_file
        self.use_async = use_async and _ASYNC_OK
        self.http = requests.Session()
        self.smart_search_enabled = smart_search
        self.smart = HybridSmartSearch(youtube_api_key, verbose=verbose) if smart_search else None
        self.ytdlp = YTDLPWrapper(ffmpeg_path=self.ffmpeg_path, verbose=self.verbose, smart=self.smart)
        self.tagger = Tagger(self.http)

    def fetch_tracks(self, playlist_id: str) -> List[Track]:
        logging.info("Fetching playlist tracks from Spotify…")
        tracks = fetch_playlist_tracks(self.sp, playlist_id)
        logging.info("Fetched %d tracks.", len(tracks))
        return tracks

    def _track_filename(self, index: int, track: Track) -> str:
        base = f"{index:02d} - {safe_filename(track.artist_str)} - {safe_filename(track.title)}"
        return base + ".mp3"

    def _queries_for(self, track: Track) -> List[str]:
        return [
            f"{track.artist_str} - {track.title} official audio",
            f"{track.title} {track.artist_str} audio",
            f"{track.title} {track.artist_str} lyrics",
        ]

    def process_one(self, payload: Tuple[int, Track, Path]) -> Tuple[int, Track, Dict[str, Any]]:
        index, track, target_dir = payload
        filename = self._track_filename(index, track)
        mp3_path = target_dir / filename

        if self.skip_existing and mp3_path.exists():
            return index, track, {"ok": True, "path": mp3_path, "skipped": True}

        temp_template = str(target_dir / (Path(filename).stem + ".%(ext)s"))
        result = None
        # Try hybrid smart search first if enabled
        if self.smart_search_enabled:
            try:
                for q in self._queries_for(track):
                    result = self.ytdlp.download_to_mp3(q, temp_template, self.bitrate, track=track, enable_smart=True)
                    if result:
                        break
            except Exception:
                result = None
        # Fallback: basic search loop
        if not result:
            for q in self._queries_for(track):
                try:
                    result = self.ytdlp.download_to_mp3(q, temp_template, self.bitrate)
                except Exception as e:  # noqa: BLE001
                    logging.debug("yt-dlp error for '%s': %s", q, e)
                    result = None
                if result:
                    break

        if result and result.exists():
            try:
                result.rename(mp3_path)
                self.tagger.embed_tags(mp3_path, track, index)
                return index, track, {"ok": True, "path": mp3_path}
            except Exception as e:  # noqa: BLE001
                return index, track, {"ok": False, "reason": f"finalize error: {e}"}
        return index, track, {"ok": False, "reason": "not found"}

    async def process_one_async(self, payload: Tuple[int, Track, Path]) -> Tuple[int, Track, Dict[str, Any]]:
        index, track, target_dir = payload
        filename = self._track_filename(index, track)
        mp3_path = target_dir / filename

        if self.skip_existing and mp3_path.exists():
            return index, track, {"ok": True, "path": mp3_path, "skipped": True}

        temp_template = str(target_dir / (Path(filename).stem + ".%(ext)s"))
        loop = asyncio.get_event_loop()
        result = None
        # Try hybrid smart search first if enabled
        if self.smart_search_enabled:
            try:
                for q in self._queries_for(track):
                    result = await loop.run_in_executor(None, self.ytdlp.download_to_mp3, q, temp_template, self.bitrate, track, True)
                    if result:
                        break
            except Exception:
                result = None
        # Fallback basic
        if not result:
            for q in self._queries_for(track):
                try:
                    result = await loop.run_in_executor(None, self.ytdlp.download_to_mp3, q, temp_template, self.bitrate)
                except Exception as e:  # noqa: BLE001
                    logging.debug("yt-dlp error for '%s': %s", q, e)
                    result = None
                if result:
                    break

        if result and result.exists():
            try:
                result.rename(mp3_path)
                # optional async cover speed-up
                if track.cover_url and _ASYNC_OK:
                    cover_bytes = await fetch_cover_bytes_async(track.cover_url)
                    if cover_bytes:
                        try:
                            id3 = ID3(str(mp3_path))
                            id3.delall('APIC')
                            id3.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_bytes))
                            id3.save()
                        except Exception:
                            pass
                self.tagger.embed_tags(mp3_path, track, index)
                return index, track, {"ok": True, "path": mp3_path}
            except Exception as e:  # noqa: BLE001
                return index, track, {"ok": False, "reason": f"finalize error: {e}"}
        return index, track, {"ok": False, "reason": "not found"}

    def write_reports(self, target_dir: Path, results: List[Tuple[int, Track, Dict[str, Any]]]) -> None:
        m3u_path = target_dir / "playlist.m3u"
        with m3u_path.open("w", encoding="utf-8") as m3u:
            for idx, _track, res in sorted(results, key=lambda r: r[0]):
                p = res.get('path')
                if p:
                    m3u.write(Path(p).name + "\n")
        logging.info("Wrote M3U: %s", m3u_path)

        report = {
            "success": [
                {
                    "index": idx,
                    "title": t.title,
                    "artists": t.artists,
                    "album": t.album,
                    "file": str(res.get('path')) if res.get('path') else None,
                    "year": t.release_year,
                    "genres": t.genres,
                }
                for idx, t, res in results if res.get('ok')
            ],
            "failed": [
                {
                    "index": idx,
                    "title": t.title,
                    "artists": t.artists,
                    "album": t.album,
                    "reason": res.get('reason'),
                }
                for idx, t, res in results if not res.get('ok')
            ],
        }
        report_path = target_dir / "download_report.json"
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logging.info("Wrote report: %s", report_path)

    def download(self, playlist_id: str) -> None:
        tracks = self.fetch_tracks(playlist_id)
        if not tracks:
            logging.error("No tracks found. Is the playlist public?")
            sys.exit(1)

        target_dir = self.out_dir / safe_filename(playlist_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        tasks = [(i, t, target_dir) for i, t in enumerate(tracks, start=1)]
        results: List[Tuple[int, Track, Dict[str, Any]]] = []

        if self.use_async and _ASYNC_OK:
            logging.info("Starting async downloads with up to %d concurrent tasks…", self.workers)
            async def runner():
                sem = asyncio.Semaphore(self.workers)
                async def guarded(task):
                    async with sem:
                        return await self.process_one_async(task)
                if _RICH:
                    console = Console()
                    with Progress(TextColumn("[bold]Tracks[/]"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(), transient=True, console=console) as progress:
                        task_id = progress.add_task("Downloading", total=len(tasks))
                        coros = [guarded(t) for t in tasks]
                        for coro in asyncio.as_completed(coros):
                            res = await coro
                            results.append(res)
                            progress.advance(task_id)
                else:
                    coros = [guarded(t) for t in tasks]
                    for coro in asyncio.as_completed(coros):
                        res = await coro
                        results.append(res)
            try:
                asyncio.run(runner())
            except KeyboardInterrupt:
                logging.warning("Interrupted by user.")
                sys.exit(130)
        else:
            logging.info("Starting downloads with %d workers…", self.workers)
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as ex:
                futures = [ex.submit(self.process_one, payload) for payload in tasks]
                if _RICH:
                    console = Console()
                    with Progress(TextColumn("[bold]Tracks[/]"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(), transient=True, console=console) as progress:
                        task_id = progress.add_task("Downloading", total=len(futures))
                        for fut in concurrent.futures.as_completed(futures):
                            try:
                                results.append(fut.result())
                            except KeyboardInterrupt:
                                raise
                            except Exception as e:  # noqa: BLE001
                                logging.exception("Worker crashed: %s", e)
                            progress.advance(task_id)
                else:
                    from tqdm import tqdm  # type: ignore
                    for fut in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="Tracks", unit="trk"):
                        try:
                            results.append(fut.result())
                        except KeyboardInterrupt:
                            raise
                        except Exception as e:  # noqa: BLE001
                            logging.exception("Worker crashed: %s", e)

        self.write_reports(target_dir, results)
        ok = sum(1 for _i, _t, r in results if r.get('ok'))
        fail = len(results) - ok
        logging.info("Done. %d succeeded, %d failed. Files saved in: %s", ok, fail, target_dir)

# ---------- CLI ----------

def detect_ffmpeg(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    common = Path(r"C:\\ffmpeg\\bin\\ffmpeg.exe")
    return str(common) if common.exists() else None


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Spotify Playlist to MP3 — Hybrid Smart Search Pro Edition")
    parser.add_argument("playlist", help="Spotify playlist URL or ID")
    parser.add_argument("--out", "-o", default="downloads", help="Output directory")
    parser.add_argument("--bitrate", "-b", type=int, default=192, help="MP3 bitrate (kbps)")
    parser.add_argument("--workers", "-w", type=int, default=2, help="Concurrent downloads")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already-downloaded tracks")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--ffmpeg", help="Path to ffmpeg binary (optional)")
    parser.add_argument("--log-file", help="Also write logs to this file (optional)")
    parser.add_argument("--async", dest="use_async", action="store_true", help="Enable async pipeline (if aiohttp available)")
    parser.add_argument("--smart-search", action="store_true", help="Use hybrid smart YouTube search (YouTube API + yt-dlp fallback)")
    parser.add_argument("--youtube-api-key", dest="youtube_api_key", help="YouTube Data API v3 key (overrides credentials.json/env)")

    args = parser.parse_args(argv)
    args = load_config(args)

    configure_logging(args.verbose, args.log_file)

    playlist_id = parse_playlist_id(args.playlist)
    ffmpeg_path = detect_ffmpeg(args.ffmpeg)

    sp = get_spotify_client()

    # resolve YouTube API key priority: CLI > env > credentials.json > config
    _cid, _cs, _redir, cred_yt = load_credentials()
    yt_key = args.youtube_api_key or os.getenv("YOUTUBE_API_KEY") or cred_yt or getattr(args, 'youtube_api_key', None)

    downloader = PlaylistDownloader(
        sp=sp,
        out_dir=Path(args.out),
        bitrate=args.bitrate,
        workers=args.workers,
        skip_existing=args.skip_existing,
        verbose=args.verbose,
        ffmpeg_path=ffmpeg_path,
        log_file=Path(args.log_file) if args.log_file else None,
        use_async=args.use_async,
        smart_search=args.smart_search,
        youtube_api_key=yt_key,
    )

    def _handle_sigint(sig, frame):  # noqa: ARG001
        logging.warning("Interrupted by user. Exiting…")
        sys.exit(130)
    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except Exception:
        pass

    downloader.download(playlist_id)


if __name__ == "__main__":
    main()
