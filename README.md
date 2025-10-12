
# Spotify to MP3  

This project allows you to download Spotify playlists as MP3 files by searching YouTube for each track and converting the audio using FFmpeg.

## Features
- Fetch tracks from Spotify playlists (public or private with OAuth)
- Download audio from YouTube using `yt-dlp`
- Convert audio to MP3 (`ffmpeg`)
- Embed ID3 tags and cover art
- Concurrent downloads for faster processing
- Automatically handles YouTube format changes and retries failed tracks
- Generates an `.m3u` playlist file automatically

## Requirements
- Python 3.11+ (3.12 recommended)
- [FFmpeg](https://ffmpeg.org/) installed or in PATH
- Python packages:
```bash
pip install spotipy yt-dlp mutagen requests tqdm
````

* A valid `credentials.json` file containing your Spotify API credentials:

```json
{
  "client_id": "your_spotify_client_id",
  "client_secret": "your_spotify_client_secret",
  "redirect_uri": "http://localhost:8080"
}
```

## Usage

```bash
python spotify_playlist_downloader.py <playlist_url_or_id> -o downloads -w 4
```

### Options

* `-o` → Output directory (default: `downloads`)
* `-w` → Number of concurrent downloads (default: 2)
* `--bitrate`, `-b` → Output MP3 bitrate (default: 192 kbps)
* `--skip-existing` → Skip already downloaded tracks
* `--verbose` → Show detailed logs

## Notes

* For private playlists or editorial playlists (like "Today's Top Hits"), you need Spotify OAuth credentials.
* Make sure `ffmpeg` is accessible by the script.
* Downloads are stored in the folder specified by `-o` (default is `downloads`).

---

## 🔄 Recent Updates (12 October 2025)

**Improved Stability & Compatibility**

* Fixed `yt-dlp` “Requested format is not available” errors by switching to a dynamic audio format selection:

  ```python
  'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best'
  ```
* Added automatic retry mechanism for transient YouTube failures.
* Added better error handling and fallback logic for unavailable formats.
* Improved compatibility with YouTube’s new `nsig` encryption system.
* Ensured FFmpeg re-encodes all outputs to the specified `--bitrate` (e.g. 192 kbps).

**Enhanced Reliability**

* Downloader now correctly prioritizes high-quality audio streams (`webm`/`m4a`).
* Output folder and filenames are safely sanitized to avoid Windows path errors.
* Added fallback logic in case the best format is temporarily unavailable.
* Clean, robust logging with `--verbose` mode for debugging.

**Result**
* The downloader now works reliably again with the latest YouTube and yt-dlp updates (as of October 2025).

