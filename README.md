# yt-transcripts-lean

A ~80-line Python script to bulk-download every transcript from a YouTube channel into a single Markdown file. No Selenium, no proxy scraping, no batch files — just `yt-dlp` for the video list and `youtube-transcript-api` for the transcripts, fetched in parallel threads.

## Why this exists

Most "bulk YouTube transcript" tools on GitHub are heavy: they spin up a headless Chrome to scrape free proxy lists, juggle 300 worker threads, and ship a `.bat` installer and a Rich-powered TUI. That's overkill for the common case — you have a channel, you want the captions, you want one Markdown file you can drop into an LLM.

This is the lean version. It does one thing.

## What it does

1. Resolves a channel URL to a flat video list with `yt-dlp` (no downloads, just metadata).
2. Fetches the English auto-captions for each video with `youtube-transcript-api`.
3. Pools 20 threads and writes a single Markdown file with TOC-friendly `## Title` sections.

Tested on channels with 250+ videos. ~5 minutes end-to-end on a residential connection, no proxies needed.

## Install

```bash
pip install yt-dlp youtube-transcript-api
```

Python 3.10+.

## Usage

```bash
python yt_transcripts.py https://www.youtube.com/@stevendux
python yt_transcripts.py https://www.youtube.com/@stevendux dux.md   # custom output path
```

Output: a single `.md` file with one `## Title` block per video, the YouTube link, and the full transcript. Videos without available captions are noted but don't fail the run.

## The technique (short version)

If you just want the recipe:

```python
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi

# 1. Get every video ID on the channel without downloading anything
with YoutubeDL({"quiet": True, "extract_flat": True, "skip_download": True}) as ydl:
    info = ydl.extract_info("https://www.youtube.com/@channel/videos", download=False)
ids = [e["id"] for e in info["entries"] if e]

# 2. Fetch captions per video
api = YouTubeTranscriptApi()
text = " ".join(s.text for s in api.fetch(ids[0], languages=["en"]))
```

Wrap that in a `ThreadPoolExecutor` and write the results to disk. That's the whole thing.

### Notes on rate limits

I did not hit YouTube rate limits pulling 264 videos with 20 threads from a normal home IP. If you do, the simplest fix is `max_workers=5` rather than reaching for a proxy pool. The proxy approach used by other tools is fragile (free proxies die fast) and adds a Chrome+Selenium dependency for what is fundamentally a polite-fetch problem.

## Output shape

```markdown
# Channel Name

Source: https://www.youtube.com/@channel
Videos: 264 (with transcripts: 252)
Downloaded: 2026-04-28 12:12

---

## Video Title 1

https://youtu.be/abc123

full transcript text here...

---

## Video Title 2
...
```

This format is intentionally simple — easy to chunk for RAG, easy to grep, easy to read in any Markdown viewer.

## Vimeo showcases

YouTube isn't the only platform with hours of free training material locked
behind a "play, don't download" UI. Vimeo showcases are common for paid
courses and webinar archives, and the transcript story there is messier:
some videos carry auto-captions, most don't, and the showcase may be
password-protected.

See [`vimeo/`](vimeo/) for a sibling pipeline that:

1. Pulls Vimeo's auto-generated `.vtt` for every video that has one.
2. Falls back to local GPU Whisper (`faster-whisper`) for the rest, with a
   parallel `yt-dlp` audio prefetcher feeding the GPU.
3. Survives crashes, log-outs, and reboots via a Windows Task Scheduler
   watchdog that auto-unregisters when the work is done.

The `vimeo/README.md` documents the non-obvious pitfalls (yt-dlp's archive
file silently doing nothing under `--skip-download`; Windows cp1252 console
crashes on Unicode video titles; producer/consumer split for keeping the GPU
fed) and how each is solved.

## License

MIT.
