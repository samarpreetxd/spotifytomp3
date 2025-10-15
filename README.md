# Spotify to MP3

This project downloads Spotify playlists as MP3 files by finding each track on YouTube and converting the audio with FFmpeg. It includes a hybrid search system (YouTube Data API v3 plus yt-dlp fallback), tagging with cover art, concurrent/async downloading, and robust logging with retries.

## Features

* Fetch tracks from Spotify playlists (public; can work with private via OAuth if you adapt auth).
* Hybrid search:

  * YouTube Data API v3 for precise matching when a key is provided.
  * Automatic fallback to yt-dlp metadata ranking when no key is available.
* MP3 conversion via FFmpeg with configurable bitrate.
* ID3 tagging (title, artist, album, track number, year, genres) and embedded cover art.
* Concurrency with threads or optional async pipeline (`--async`).
* Structured logging, optional JSON logs, rotating file logs, and secret redaction.
* Retries, basic rate limiting, and defensive fallbacks.
* Safe filename sanitization for cross-platform paths.
* Outputs:

  * `playlist.m3u` (ordered track list)
  * `download_report.json` (success/failed entries with metadata)

## Requirements

* Python 3.11+ (3.12 recommended)
* FFmpeg installed or available on PATH
* Python packages:

```bash
pip install -r requirements.txt
```

## Credentials

Create a `credentials.json` in the project directory or set environment variables.

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

## Configuration file (.spotifydlrc)

You can set defaults in either `~/.spotifydlrc` or `./.spotifydlrc`.

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

CLI flags always override config values.

## Usage

Basic:

```bash
python spotify_playlist_to_mp3_hybrid.py <playlist_url_or_id> -o downloads -w 4
```

High-performance hybrid search:

```bash
python spotify_playlist_to_mp3_hybrid.py <playlist_url_or_id> -w 6 --async --smart-search -o downloads
```

If you prefer to pass the YouTube API key on the CLI:

```bash
python spotify_playlist_to_mp3_hybrid.py <playlist_url_or_id> --smart-search --youtube-api-key YOUR_KEY
```

### Options

* `-o`, `--out` Output directory (default: `downloads`)
* `-w`, `--workers` Number of concurrent downloads (default: 2)
* `-b`, `--bitrate` MP3 bitrate kbps (default: 192)
* `--skip-existing` Skip already downloaded tracks
* `--verbose` Verbose logging
* `--ffmpeg` Path to ffmpeg binary (Windows users may need this)
* `--log-file` Path to write rotating logs (in addition to console)
* `--async` Enable async pipeline (requires `aiohttp`)
* `--smart-search` Enable hybrid YouTube matching (API + yt-dlp fallback)
* `--youtube-api-key` Override key from env/config/credentials.json

## Notes

* Windows: if FFmpeg is not on PATH, set `--ffmpeg` (the script will also detect `C:\ffmpeg\bin\ffmpeg.exe` if present).
* Output files are written to `<out>/<playlist_id_sanitized>/`.
* Filenames are sanitized and prefixed with track index for stable ordering.

## Recent Updates (12 October 2025)

Improved stability and compatibility:

* Resolved yt-dlp “Requested format is not available” by using dynamic format selection:

  ```python
  'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best'
  ```
* Automatic retries for transient YouTube failures.
* Better error handling and fallbacks for unavailable formats.
* Compatibility improvements for YouTube’s `nsig` changes.
* Ensured FFmpeg re-encodes to the selected `--bitrate`.

Enhanced reliability:

* Preference for high-quality audio streams (`webm`/`m4a`).
* Safer output paths and filenames.
* Stronger fallback logic if best formats are temporarily unavailable.
* Clearer logging with `--verbose`.

Result:

* Reliable operation with current YouTube and yt-dlp (as of October 2025).

## Recent Updates (15 October 2025)

Hybrid Smart Search and performance:

* Introduced `--smart-search`:

  * Uses YouTube Data API v3 to rank candidate videos by channel/title match, “official” signals, and duration proximity.
  * Falls back to yt-dlp search when API is unavailable or scores are insufficient.
* Added `--async` mode using `asyncio` and `aiohttp` to parallelize I/O; guarded by a semaphore to respect system limits.
* Threaded path remains available; choose `-w` to control concurrency.

Metadata and tagging:

* Enriched ID3 tags with album, track number, year, and top genres (via batched artist lookups).
* Embedded cover art from Spotify album images; async fast-path for cover fetch when available.

Resilience and safety:

* Unified retry decorator (Tenacity when installed; builtin exponential backoff otherwise).
* Optional simple rate limiting around Spotify calls.
* Redacting formatter hides secrets and long tokens in logs.
* Structured JSON logs when `python-json-logger` is installed; rotating file logging via `--log-file`.

Configuration and DX:

* `.spotifydlrc` support for local or global defaults.
* `download_report.json` summarizing successes/failures with metadata.
* `playlist.m3u` generated in final order.
* Safer filename sanitizer with length limits and whitespace normalization.

Internal improvements:

* Clear separation of concerns:

  * `HybridSmartSearch` for URL resolution.
  * `YTDLPWrapper` for download/convert.
  * `Tagger` for ID3 and cover art.
  * `PlaylistDownloader` for orchestration, threading/async, and reporting.
* Defensive handling of optional dependencies; graceful degradation when extras are missing.

## Example credentials.json

```json
{
  "client_id": "your_spotify_client_id",
  "client_secret": "your_spotify_client_secret",
  "redirect_uri": "http://localhost:8888/callback",
  "youtube_api_key": "YOUR_YT_DATA_API_KEY"
}
```

## Outputs

* `playlist.m3u`
* `download_report.json`
* MP3 files named as:

  ```
  NN - Artist 1, Artist 2 - Title.mp3
  ```

## Troubleshooting

* Missing FFmpeg: install FFmpeg and ensure the binary is discoverable or pass `--ffmpeg`.
* Spotify credentials missing: set env vars or create `credentials.json`.
* YouTube API quota errors: omit `--smart-search` to use yt-dlp fallback only, or supply a valid key.
* Rate limits: reduce `-w` or disable `--async` on low-resource systems.

## License

This project depends on third-party services and tools (Spotify, YouTube, yt-dlp, FFmpeg). Ensure your use complies with applicable terms and local laws.
