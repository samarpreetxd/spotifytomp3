# Spotify to MP3

This project downloads Spotify playlists as MP3 files by finding each track on YouTube and converting the audio with FFmpeg.  
It includes a hybrid search system (YouTube Data API v3 + yt-dlp fallback), tagging with cover art, concurrency (threaded or async),  
and robust structured logging with automatic retries.

---

## 🚀 Features

* Fetch tracks from public Spotify playlists (supports genre enrichment via artist lookups).
* **Hybrid Smart Search**:
  * YouTube Data API v3 for precise matching when an API key is available.
  * Automatic fallback to yt-dlp metadata ranking when no key is present.
* MP3 conversion via FFmpeg with configurable bitrate.
* Full ID3 tagging (title, artist, album, year, track number, genres) + embedded cover art.
* Concurrency with threads or optional async pipeline (`--async`).
* Structured logging, optional JSON logs, rotating file logs, and secret redaction.
* Retries, basic rate limiting, and defensive fallback logic.
* Safe filename sanitization and auto-length clamping.
* Generates:
  * `playlist.m3u` (ordered track list)
  * `download_report.json` (metadata for success/failure)

---

## 🧰 Requirements

* **Python 3.11+** (3.12 recommended)
* **FFmpeg** installed or on PATH
* Python dependencies:

```bash
pip install -r requirements.txt
````

---

## 🔐 Credentials

Create a `credentials.json` file or set environment variables.

```json
{
  "client_id": "your_spotify_client_id",
  "client_secret": "your_spotify_client_secret",
  "redirect_uri": "http://localhost:8888/callback",
  "youtube_api_key": "YOUR_YT_DATA_API_KEY"
}
```

Environment variable alternatives:

* `SPOTIFY_CLIENT_ID`
* `SPOTIFY_CLIENT_SECRET`
* `SPOTIFY_REDIRECT_URI`
* `YOUTUBE_API_KEY`

---

## ⚙️ Configuration (.spotifydlrc)

Defaults can be placed in either `~/.spotifydlrc` or `./.spotifydlrc`.

```ini
[defaults]
out = downloads
bitrate = 192
workers = 4
skip_existing = true
ffmpeg_path =
verbose = false
async = true
log_file = logs/run.log
youtube_api_key =
```

> CLI flags always override config values.

---

## 💻 Usage

### Basic

```bash
python spotify_playlist_to_mp3_hybrid.py <playlist_url_or_id> -o downloads -w 4
```

### Hybrid Smart Search + Async

```bash
python spotify_playlist_to_mp3_hybrid.py <playlist_url_or_id> -w 6 --async --smart-search -o downloads
```

### Specify YouTube API Key on CLI

```bash
python spotify_playlist_to_mp3_hybrid.py <playlist_url_or_id> --smart-search --youtube-api-key YOUR_KEY
```

### Dry Run (list only)

```bash
python spotify_playlist_to_mp3_hybrid.py <playlist_url_or_id> --dry-run
```

---

## ⚡ Options

* `-o`, `--out` — Output directory (default: `downloads`)
* `-w`, `--workers` — Number of concurrent downloads (default: 2)
* `-b`, `--bitrate` — MP3 bitrate kbps (default: 192)
* `--skip-existing` — Skip already-downloaded tracks
* `--verbose` — Verbose logging
* `--ffmpeg` — Path to ffmpeg binary (Windows users may need this)
* `--log-file` — Path for rotating logs
* `--async` — Enable async mode (requires `aiohttp`)
* `--smart-search` — Enable hybrid YouTube matching (API + yt-dlp fallback)
* `--youtube-api-key` — Override YouTube API key
* `--dry-run` — List all tracks without downloading

---

## 🪟 Windows Notes

* The script now automatically detects FFmpeg from:

  1. `--ffmpeg` CLI argument
  2. `FFMPEG_PATH` environment variable
  3. System PATH (`ffmpeg` or `ffmpeg.exe`)
  4. Common path `C:\ffmpeg\bin\ffmpeg.exe`

If FFmpeg is not found, the script will warn but continue.

---

## 🧩 Outputs

* `playlist.m3u` — ordered track listing
* `download_report.json` — JSON summary of successes/failures
* MP3s named as:

```
NN - Artist 1, Artist 2 - Title.mp3
```

---

## 🧠 Recent Updates (20 October 2025)

### New Features

* Added `--dry-run` flag to list Spotify playlist tracks without downloading.
* Automatic FFmpeg detection:

  * Checks `--ffmpeg`, `FFMPEG_PATH`, PATH, and common Windows locations.
* Argument validation:

  * Bitrate clamped to `32–320 kbps`.
  * Worker count validated (`>=1`).
* More detailed logging for async/threaded download progress.
* Cleaner log output messages and improved readability.

### Improvements

* Revised startup messages and argument checks.
* Enhanced FFmpeg detection reliability on Windows and macOS.
* Consistent handling of optional dependencies.
* Updated retry logic for transient network issues.
* Simplified playlist ID parsing.
* Better defensive coding across all modules.

### Result

✅ More stable, flexible, and user-friendly.
Perfect for both automation scripts and manual CLI use.

---

## 🧩 Example credentials.json

```json
{
  "client_id": "your_spotify_client_id",
  "client_secret": "your_spotify_client_secret",
  "redirect_uri": "http://localhost:8888/callback",
  "youtube_api_key": "YOUR_YT_DATA_API_KEY"
}
```

---

## 🧾 Troubleshooting

* **FFmpeg not found** → install and ensure it’s on PATH or pass via `--ffmpeg`.
* **Spotify credentials missing** → set env vars or use `credentials.json`.
* **YouTube API quota errors** → omit `--smart-search` to rely on yt-dlp fallback.
* **Slow downloads or rate limits** → lower `-w` or disable `--async`.
* **Invalid bitrate** → automatically corrected to 192 kbps.

---

## 📜 License

This project uses Spotify, YouTube, yt-dlp, and FFmpeg APIs/tools.
Ensure compliance with their respective terms of service and local copyright laws.


