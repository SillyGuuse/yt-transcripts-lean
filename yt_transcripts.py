"""
Minimal YouTube channel transcript downloader.

Usage:
    python yt_transcripts.py <channel_url> [output.md]

Examples:
    python yt_transcripts.py https://www.youtube.com/@stevendux
    python yt_transcripts.py https://www.youtube.com/@stevendux dux.md

No proxies, no Selenium, no batch files. Just yt-dlp for the video list and
youtube-transcript-api for transcripts, fetched in parallel threads.
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    CouldNotRetrieveTranscript,
)


def list_videos(channel_url: str) -> tuple[str, list[dict]]:
    """Return (channel_name, [{'id', 'title'}, ...]) for the channel."""
    if not channel_url.rstrip("/").endswith("/videos"):
        channel_url = channel_url.rstrip("/") + "/videos"

    opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    name = info.get("title", "channel").replace(" - Videos", "")
    entries = info.get("entries") or []
    videos = [
        {"id": e["id"], "title": e.get("title", e["id"])}
        for e in entries
        if e and e.get("id")
    ]
    return name, videos


def fetch_transcript(video_id: str) -> str | None:
    """Return plain transcript text, or None if unavailable."""
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["en"])
        return " ".join(snippet.text for snippet in fetched).strip()
    except (NoTranscriptFound, TranscriptsDisabled, CouldNotRetrieveTranscript):
        return None
    except Exception:
        return None


def slug(text: str) -> str:
    return re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "-").lower()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    channel_url = sys.argv[1]
    out_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else None

    print(f"Fetching video list from {channel_url} ...")
    channel_name, videos = list_videos(channel_url)
    total = len(videos)
    print(f"Found {total} videos for '{channel_name}'.")

    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(f"{slug(channel_name)}_transcripts_{stamp}.md")

    results: dict[str, str | None] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(fetch_transcript, v["id"]): v for v in videos}
        for fut in as_completed(futures):
            v = futures[fut]
            results[v["id"]] = fut.result()
            done += 1
            status = "OK " if results[v["id"]] else "--"
            print(f"[{done}/{total}] {status} {v['title'][:70]}")

    with_text = sum(1 for t in results.values() if t)
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# {channel_name}\n\n")
        f.write(f"Source: {channel_url}\n")
        f.write(f"Videos: {total} (with transcripts: {with_text})\n")
        f.write(f"Downloaded: {datetime.now():%Y-%m-%d %H:%M}\n\n---\n\n")
        for v in videos:
            text = results.get(v["id"])
            f.write(f"## {v['title']}\n\n")
            f.write(f"https://youtu.be/{v['id']}\n\n")
            if text:
                f.write(text + "\n\n")
            else:
                f.write("_(no transcript available)_\n\n")
            f.write("---\n\n")

    print(f"\nWrote {out_path} ({with_text}/{total} with transcripts)")


if __name__ == "__main__":
    main()
