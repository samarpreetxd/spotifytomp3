
# Spotify to MP3

This project allows you to download Spotify playlists as MP3 files by searching YouTube for each track and converting the audio using FFmpeg.

## Features
- Fetch tracks from Spotify playlists (public or private with OAuth)
- Download audio from YouTube using `yt-dlp`
- Convert audio to MP3 (`ffmpeg`)
- Embed ID3 tags and cover art
- Concurrent downloads for faster processing

## Requirements
- Python 3.11+ (3.12 recommended)
- [FFmpeg](https://ffmpeg.org/) installed or in PATH
- Python packages:
```bash
  pip install spotipy yt-dlp mutagen requests tqdm
````

## Usage

```bash
python spotify_playlist_downloader.py <playlist_url_or_id> -o downloads -w 4
```

### Options

* `-o` → Output directory (default: `downloads`)
* `-w` → Number of concurrent downloads (default: 2)
* `--skip-existing` → Skip already downloaded tracks
* `--verbose` → Show detailed logs

## Notes

* For private playlists or editorial playlists (like "Today's Top Hits"), you need Spotify OAuth credentials.
* Make sure `ffmpeg` is accessible by the script.
* Downloads are stored in the folder specified by `-o` (default is `downloads`).

