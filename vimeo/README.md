# Vimeo showcase transcript extractor

Bulk-transcribe every video in a Vimeo showcase (public or password-protected).
First grabs auto-generated captions where available; falls back to local
GPU Whisper for the rest. Designed for showcases with **hundreds of videos**.

Real run: a 580-video password-protected showcase, ~14 hours unattended on a
laptop with an RTX 4060. 184 videos had Vimeo captions; 396 needed Whisper.

## What's here

| File | Role |
|---|---|
| `extract_captions.py` | Pulls every available `.vtt` and converts to clean `.txt` |
| `download_audio.py` | Parallel `yt-dlp` worker — pre-fetches audio for caption-less videos |
| `transcribe_whisper.py` | GPU consumer — transcribes whatever audio is ready, then exits |
| `supervisor.py` | Re-runs `transcribe_whisper` until the to-do list is empty |
| `watchdog.ps1` | Optional Windows Task Scheduler entry — restarts the chain on reboot |

## Requirements

```bash
pip install yt-dlp faster-whisper
# plus ffmpeg on PATH (winget install Gyan.FFmpeg, brew install ffmpeg, etc.)
```

For password-protected showcases:

```bash
export VIMEO_PASSWORD=yourpassword           # bash / zsh
$env:VIMEO_PASSWORD = "yourpassword"          # PowerShell
```

## Quickstart

```bash
# 1. Grab everything that has Vimeo-side auto-captions (fast, no GPU)
python extract_captions.py https://vimeo.com/showcase/123456 --watch

# 2. Pre-download audio for the rest, in parallel (run this in a second shell)
python download_audio.py https://vimeo.com/showcase/123456 --workers 4

# 3. Transcribe everything in audio/ with the GPU; restart until empty
python supervisor.py
```

Outputs land in `txt/` — one `.txt` per video, named
`NNN - <title>.txt` (zero-padded playlist index keeps them sortable and the
pipeline idempotent across re-runs).

---

## Techniques worth stealing

The non-obvious problems we hit, and how each script solves them.

### 1. yt-dlp's `--download-archive` lies when you use `--skip-download`

The natural way to make a yt-dlp run resumable is `--download-archive done.txt`.
But yt-dlp **only writes to the archive after a successful media download** —
when you pass `--skip-download` (because you only want subtitles), the archive
stays empty. Every re-run re-processes every video from scratch.

**Fix in `extract_captions.py`:** track attempted indices ourselves with
yt-dlp's `--print-to-file "%(playlist_index)s" attempted.txt`. That writes
one line per item yt-dlp touches, regardless of download outcome. We union
that file with the set of `.vtt` files actually on disk to compute the next
run's `--playlist-items` spec — typically a compact `7-12,15,20-580` style
range so even a 580-video showcase resumes in milliseconds.

### 2. Most YouTube/Vimeo bulk extractors download serially

`yt-dlp` is single-threaded per invocation. For 400 audio downloads averaging
30 seconds each that's 200 minutes of dead time. Worse, if you run
Whisper inline (download → transcribe → download → transcribe), the GPU sits
idle ~80% of the time waiting for the network.

**Fix:** split into producer + consumer.

- `download_audio.py` runs **N parallel `yt-dlp` subprocesses** (default 4)
  via a `ThreadPoolExecutor`. Each one also gets `-N 4` so its own fragment
  fetches are concurrent. ~12 sec/video sustained on a residential link.
- `transcribe_whisper.py` is a **stateless one-pass consumer**. It scans
  the manifest, skips any index whose audio isn't downloaded yet, and just
  exits when it runs out. The GPU is fed continuously.

The two halves coordinate purely through the filesystem (`audio/*.mp3`
appearing). No shared state, no message queue, trivial to reason about.

### 3. Long-running scripts on Windows die in surprising ways

Things that killed the run during development:

- `print()` of a title containing `⧸` (U+29F8) crashed Python because the
  default Windows console codepage is cp1252.
- A single video's audio took >30 min to download and hung the worker.
- The user logged out for the night.
- A laptop sleep cycle dropped network in the middle of a fragment.

**Fixes layered in `supervisor.py` + `watchdog.ps1`:**

1. **Per-video `try/except`** in `transcribe_whisper` — one bad video writes
   a `.failed` marker, the loop continues.
2. `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at script
   start — silently downgrades unprintable characters instead of crashing.
3. **Per-call timeout** on each `yt-dlp` invocation in `download_audio` (30
   minutes). Stuck downloads get marked failed and the pool moves on.
4. **`supervisor.py`** restarts `transcribe_whisper` after any exit until
   the dry-run check reports `Whisper-needed: 0`. Bails after 30 consecutive
   zero-progress restarts (a true wedge, not just "downloader is slow").
5. **`watchdog.ps1`** is registered with Windows Task Scheduler to fire
   every 5 minutes. If the supervisor or downloader process is gone, it
   relaunches them with `Start-Process -WindowStyle Hidden` (detached from
   any shell). Survives log-out and reboot. When the work finishes, the
   watchdog **unregisters its own scheduled task** so it doesn't keep firing
   forever.

### 4. Resume semantics that handle every interrupt

The to-do list for any of the three workers is computed the same way every
time, from the filesystem:

```python
needs_caption = playlist_index not in {parse(f.name) for f in subs.glob("*.vtt")}
needs_whisper = needs_caption and not any(txt.glob(f"{idx:03d} - *.txt"))
needs_audio   = needs_whisper and not any(audio.glob(f"{idx:03d} - *.mp3"))
```

There is no separate "progress database". If you `rm -rf audio/`, the
downloader does the right thing. If you delete a single bad `.txt`, that one
video gets re-transcribed on the next supervisor pass. Restartability is a
property of the data layout, not the code.

### 5. Whisper model + compute-type defaults that actually work on consumer GPUs

`faster-whisper` with `medium` + `float16` on an 8 GB RTX 4060 transcribes a
1-hour audio file in ~5 minutes (vs. ~1.5–2 hours on CPU). When the GPU isn't
available, the script catches the load error and falls back to
`device=cpu, compute_type=int8` so the same command works on a laptop without
CUDA. No flag-tuning required for the common cases.

---

## Limitations

- **One showcase URL = one run directory.** The state lives next to the
  scripts (`subs/`, `txt/`, `audio/`, `attempted.txt`, `manifest.tsv`).
  To work on a second showcase, copy the `vimeo/` folder.
- **Vimeo subtitle naming.** Auto-captions come back as `en-x-autogen`; the
  script's `--sub-langs` glob covers that plus plain `en` / `en-US`. Other
  languages need an extra entry.
- **Disk usage during the Whisper phase** is roughly `videos × 75 MB` (mp3
  q=5). For 400 videos plan on ~30 GB free; `transcribe_whisper.py` deletes
  each `.mp3` after it succeeds unless you pass `--keep-audio`.
