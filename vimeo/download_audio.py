"""
Parallel audio prefetcher for Vimeo showcase videos that lack captions.

Runs alongside transcribe_whisper.py so the GPU never waits on yt-dlp.
For each video that needs Whisper (no caption .vtt, no .txt yet), if an
.mp3 isn't already present in audio/, download it. N workers run yt-dlp
in parallel.

Requires ffmpeg on PATH (yt-dlp uses it for the mp3 extraction).

Usage:
    export VIMEO_PASSWORD=...
    python download_audio.py <showcase_url> --workers 4
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent
SUBS_DIR = ROOT / "subs"
TXT_DIR = ROOT / "txt"
AUDIO_DIR = ROOT / "audio"
DL_FAIL_DIR = ROOT / "download_failed"
MANIFEST = ROOT / "manifest.tsv"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def has_caption(idx: int) -> bool:
    return any(SUBS_DIR.glob(f"{idx:03d} - *.vtt"))


def has_text(idx: int) -> bool:
    return any(TXT_DIR.glob(f"{idx:03d} - *.txt"))


def has_audio(idx: int) -> bool:
    return any(AUDIO_DIR.glob(f"{idx:03d} - *.mp3"))


def has_dl_failed(idx: int) -> bool:
    return (DL_FAIL_DIR / f"{idx:03d}.failed").exists()


def build_manifest(showcase_url: str, password: str | None) -> list[tuple[int, str]]:
    """Return [(playlist_index, video_id), ...] using a single fast network call."""
    if MANIFEST.exists():
        rows: list[tuple[int, str]] = []
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit():
                rows.append((int(parts[0]), parts[1]))
        return rows

    print("Building manifest via yt-dlp --flat-playlist ...")
    cmd = [
        "yt-dlp", "--flat-playlist",
        "--print", "%(playlist_index)s\t%(id)s\t%(title)s",
        showcase_url,
    ]
    if password:
        cmd[1:1] = ["--video-password", password]
    out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
    rows = []
    with MANIFEST.open("w", encoding="utf-8") as f:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit():
                rows.append((int(parts[0]), parts[1]))
                f.write(line.rstrip() + "\n")
    print(f"Manifest: {len(rows)} entries")
    return rows


def download_one(showcase_url: str, password: str | None,
                 idx: int, vid: str) -> tuple[int, bool, str]:
    AUDIO_DIR.mkdir(exist_ok=True)
    fname_tpl = f"{idx:03d} - %(title).180B.%(ext)s"
    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
        "-x", "--audio-format", "mp3",
        "--audio-quality", "5",
        "--no-warnings",
        "--ignore-errors",
        "-N", "4",  # concurrent fragment downloads within a single yt-dlp
        "-o", str(AUDIO_DIR / fname_tpl),
        f"{showcase_url}/video/{vid}",
    ]
    if password:
        cmd[1:1] = ["--video-password", password]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1800)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        return (idx, False, "timeout")
    if rc != 0 or not has_audio(idx):
        tail = (proc.stderr or proc.stdout or "")[-400:]
        return (idx, False, f"rc={rc} {tail}")
    return (idx, True, "")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("showcase_url")
    p.add_argument("--password", default=os.environ.get("VIMEO_PASSWORD"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    DL_FAIL_DIR.mkdir(exist_ok=True)
    AUDIO_DIR.mkdir(exist_ok=True)

    rows = build_manifest(args.showcase_url, args.password)
    todo = [
        (idx, vid) for idx, vid in rows
        if not has_caption(idx) and not has_text(idx)
        and not has_audio(idx) and not has_dl_failed(idx)
    ]
    print(f"To download: {len(todo)}  (workers={args.workers})", flush=True)
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        return 0

    t0 = time.time()
    successes = failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(download_one, args.showcase_url, args.password, idx, vid)
                   for idx, vid in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            idx, ok, err = fut.result()
            if ok:
                successes += 1
                tag = "ok"
            else:
                failures += 1
                tag = f"FAIL ({err[:120]})"
                (DL_FAIL_DIR / f"{idx:03d}.failed").write_text(err + "\n", encoding="utf-8")
            elapsed = time.time() - t0
            rate = n / elapsed if elapsed else 0
            print(f"[{n}/{len(todo)}] #{idx:03d} {tag}  "
                  f"({successes} ok, {failures} fail, {rate:.2f}/s)", flush=True)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
