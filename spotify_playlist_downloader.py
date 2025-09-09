

import os
import re
import sys
import json
import logging
import argparse
import concurrent.futures
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from yt_dlp import YoutubeDL
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from tqdm import tqdm

@dataclass
class Track:
    id: str
    title: str
    artists: List[str]
    album: Optional[str]
    duration_ms: int
    cover_url: Optional[str]

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists)

INVALID_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def safe_filename(s: str, maxlen: int = 120) -> str:
    s = s.strip()
    s = INVALID_FN.sub('', s)
    if len(s) > maxlen:
        s = s[:maxlen].rsplit(' ', 1)[0]
    return s

def parse_playlist_id(url_or_id: str) -> str:
    if 'playlist' in url_or_id:
        m = re.search(r'playlist[/:]([A-Za-z0-9]+)', url_or_id)
        if m:
            return m.group(1)
    if url_or_id.startswith('spotify:playlist:'):
        return url_or_id.split(':')[-1]
    return url_or_id

def load_credentials(path="credentials.json"):
    if not os.path.exists(path):
        logging.error("credentials.json not found!")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("client_id"), data.get("client_secret"), data.get("redirect_uri")

def get_spotify_client() -> spotipy.Spotify:
    client_id, client_secret, redirect_uri = load_credentials()
    if not client_id or not client_secret:
        logging.error("Invalid credentials in credentials.json")
        sys.exit(1)
    creds = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    return spotipy.Spotify(client_credentials_manager=creds)

def fetch_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> List[Track]:
    items = []
    limit, offset = 100, 0
    while True:
        res = sp.playlist_items(
            playlist_id, offset=offset, limit=limit,
            fields='items.track(id,name,artists.name,album(name,images),duration_ms),next'
        )
        if not res or 'items' not in res:
            break
        for it in res['items']:
            t = it.get('track')
            if not t:
                continue
            artists = [a['name'] for a in t.get('artists', [])]
            cover_url = None
            imgs = t.get('album', {}).get('images', [])
            if imgs:
                cover_url = imgs[0]['url']
            items.append(Track(
                id=t.get('id'),
                title=t.get('name'),
                artists=artists,
                album=t.get('album', {}).get('name'),
                duration_ms=t.get('duration_ms'),
                cover_url=cover_url
            ))
        offset += len(res['items'])
        if not res.get('next'):
            break
    return items

def download_track_yt(search_query: str, out_path_template: str, bitrate_kbps: int = 192, verbose: bool = False) -> Optional[Path]:
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': out_path_template,
        'noplaylist': True,
        'quiet': not verbose,
        'no_warnings': True,
        'default_search': 'ytsearch1',
        'ffmpeg_location': r'C:\ffmpeg\bin\ffmpeg.exe',
        'postprocessors': [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': str(bitrate_kbps)},
            {'key': 'FFmpegMetadata'}
        ],
        'retries': 3,
        'noprogress': True,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=True)
            if isinstance(info, dict) and info.get('entries'):
                info = info['entries'][0]
            out_dir = Path(out_path_template).parent
            mp3s = sorted(out_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
            return mp3s[0] if mp3s else None
    except Exception as e:
        if verbose:
            logging.exception("yt-dlp failed for: %s -> %s", search_query, e)
    return None

def embed_tags(mp3_path: Path, track: Track, index: int):
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
        audio.save()

        if track.cover_url:
            r = requests.get(track.cover_url, timeout=10)
            if r.status_code == 200:
                id3 = ID3(str(mp3_path))
                id3.delall('APIC')
                id3.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=r.content))
                id3.save()
    except Exception as e:
        logging.debug("Tagging failed: %s", e)

def process_track(track: Track, out_dir: Path, index: int, bitrate: int, skip_existing: bool, verbose: bool):
    filename_base = f"{index:02d} - {safe_filename(track.artist_str)} - {safe_filename(track.title)}"
    mp3_path = out_dir / (filename_base + ".mp3")

    if skip_existing and mp3_path.exists():
        return {'ok': True, 'path': mp3_path, 'skipped': True}

    queries = [
        f"{track.artist_str} - {track.title} official audio",
        f"{track.title} {track.artist_str} audio",
        f"{track.title} {track.artist_str} lyrics"
    ]

    temp_template = str(out_dir / (filename_base + ".%(ext)s"))
    for q in queries:
        mp3_result = download_track_yt(q, temp_template, bitrate_kbps=bitrate, verbose=verbose)
        if mp3_result:
            try:
                mp3_result.rename(mp3_path)
                embed_tags(mp3_path, track, index)
                return {'ok': True, 'path': mp3_path}
            except Exception as e:
                return {'ok': False, 'reason': f"finalize error: {e}"}
    return {'ok': False, 'reason': 'not found'}

def main():
    parser = argparse.ArgumentParser(description="Spotify Playlist to MP3 (via YouTube)")
    parser.add_argument("playlist", help="Spotify playlist URL or ID")
    parser.add_argument("--out", "-o", default="downloads", help="Output directory")
    parser.add_argument("--bitrate", "-b", type=int, default=192, help="MP3 bitrate (kbps)")
    parser.add_argument("--workers", "-w", type=int, default=2, help="Concurrent downloads")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already-downloaded tracks")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    playlist_id = parse_playlist_id(args.playlist)
    sp = get_spotify_client()

    logging.info("Fetching playlist tracks from Spotify...")
    tracks = fetch_playlist_tracks(sp, playlist_id)
    if not tracks:
        logging.error("No tracks found. Is the playlist public?")
        sys.exit(1)

    playlist_folder = Path(args.out) / safe_filename(playlist_id)
    playlist_folder.mkdir(parents=True, exist_ok=True)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {ex.submit(process_track, t, playlist_folder, idx, args.bitrate, args.skip_existing, args.verbose): (idx, t)
                   for idx, t in enumerate(tracks, start=1)}

        for fut in tqdm(concurrent.futures.as_completed(futures), total=len(tracks), desc="Tracks"):
            idx, t = futures[fut]
            try:
                res = fut.result()
                results.append((idx, t, res))
                if res.get('ok'):
                    logging.info("Track %02d OK: %s - %s", idx, t.artist_str, t.title)
                else:
                    logging.warning("Track %02d FAILED: %s - %s (%s)", idx, t.artist_str, t.title, res.get('reason'))
            except Exception as e:
                logging.exception("Worker crashed for track %d: %s", idx, e)

    # Create M3U
    m3u_path = playlist_folder / "playlist.m3u"
    with m3u_path.open("w", encoding="utf-8") as m3u:
        for idx, track, res in sorted(results, key=lambda r: r[0]):
            p = res.get('path')
            if p:
                m3u.write(str(p.name) + "\n")

    logging.info("Done. Files saved in: %s", playlist_folder)

if __name__ == "__main__":
    main()
