"""
Bulk-download auto-generated captions for every video in a Vimeo showcase
(public or password-protected).

Usage:
    export VIMEO_PASSWORD=...                   # only if showcase is gated
    python extract_captions.py <showcase_url>   # e.g. https://vimeo.com/showcase/123456

Outputs:
    subs/  one .vtt per video that had captions
    txt/   the same file as plain text (timestamps + duplicate rolling-caption
           lines stripped)
    attempted.txt  ledger of every playlist index yt-dlp has touched, so a
           re-run only re-attempts indices we've never seen yet (fast resume,
           important when most videos lack captions)

Why the ledger? yt-dlp does NOT update --download-archive when used with
--skip-download, so the obvious "resume from archive" approach silently
re-processes every video on every run. Instead we use yt-dlp's
--print-to-file to record every playlist_index it touches; we union that
with the set of indices that actually produced a .vtt to build the next
run's --playlist-items spec.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
SUBS_DIR = ROOT / "subs"
TXT_DIR = ROOT / "txt"
ATTEMPTED = ROOT / "attempted.txt"

INDEX_RE = re.compile(r"^(\d{3})\s-\s")
TIMESTAMP_RE = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->")
TAG_RE = re.compile(r"<[^>]+>")


def downloaded_indices() -> set[int]:
    if not SUBS_DIR.exists():
        return set()
    return {int(m.group(1)) for f in SUBS_DIR.glob("*.vtt")
            if (m := INDEX_RE.match(f.name))}


def attempted_indices() -> set[int]:
    if not ATTEMPTED.exists():
        return set()
    return {int(line) for line in ATTEMPTED.read_text(encoding="utf-8").splitlines()
            if line.strip().isdigit()}


def missing_ranges(done: set[int], total: int) -> str:
    """Compact yt-dlp --playlist-items spec: '7-12,15,20-580'."""
    missing = sorted(set(range(1, total + 1)) - done)
    if not missing:
        return ""
    parts: list[str] = []
    start = prev = missing[0]
    for n in missing[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def get_total(showcase_url: str, password: str | None) -> int:
    cmd = ["yt-dlp", "--flat-playlist", "--print", "%(playlist_count)s", showcase_url]
    if password:
        cmd[1:1] = ["--video-password", password]
    out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
    for line in out.splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    raise SystemExit("Could not determine playlist size")


def run_ytdlp(showcase_url: str, password: str | None, items_spec: str) -> int:
    SUBS_DIR.mkdir(exist_ok=True)
    cmd = [
        "yt-dlp",
        "--write-subs",
        "--write-auto-subs",
        # Vimeo's auto-generated captions are tagged "en-x-autogen"; the wider
        # globs cover sites that use plain "en" or "en-US".
        "--sub-langs", "en-x-autogen,en.*,en",
        "--sub-format", "vtt",
        "--skip-download",
        "--convert-subs", "vtt",
        "--ignore-errors",
        "--no-warnings",
        "--playlist-items", items_spec,
        "--print-to-file", "%(playlist_index)s", str(ATTEMPTED),
        "-o", str(SUBS_DIR / "%(playlist_index)03d - %(title).180B.%(ext)s"),
        showcase_url,
    ]
    if password:
        cmd.insert(1, password)
        cmd.insert(1, "--video-password")
    return subprocess.call(cmd)


def vtt_to_text(vtt_path: Path) -> str:
    out: list[str] = []
    last = ""
    for raw in vtt_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s == "WEBVTT" or s.startswith(("NOTE", "Kind:", "Language:")):
            continue
        if TIMESTAMP_RE.search(s) or s.isdigit():
            continue
        s = TAG_RE.sub("", s)
        if not s or s == last:
            continue
        out.append(s)
        last = s
    return "\n".join(out) + "\n"


def convert_all() -> int:
    TXT_DIR.mkdir(exist_ok=True)
    converted = 0
    for vtt in sorted(SUBS_DIR.glob("*.vtt")):
        stem = vtt.name
        for suffix in (".en-x-autogen.vtt", ".en.vtt", ".vtt"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        out = TXT_DIR / f"{stem}.txt"
        if out.exists() and out.stat().st_mtime >= vtt.stat().st_mtime:
            continue
        out.write_text(vtt_to_text(vtt), encoding="utf-8")
        converted += 1
    return converted


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("showcase_url",
                   help="e.g. https://vimeo.com/showcase/123456")
    p.add_argument("--password", default=os.environ.get("VIMEO_PASSWORD"),
                   help="overrides $VIMEO_PASSWORD")
    p.add_argument("--total", type=int, default=0,
                   help="skip auto-detection of playlist size")
    p.add_argument("--watch", action="store_true",
                   help="loop until every index has been attempted")
    p.add_argument("--watch-sleep", type=int, default=15)
    p.add_argument("--convert-only", action="store_true")
    args = p.parse_args()

    if args.convert_only:
        print(f"Converted {convert_all()} new file(s)")
        return 0

    total = args.total or get_total(args.showcase_url, args.password)
    print(f"Showcase has {total} videos")

    stalled = 0
    while True:
        done = downloaded_indices()
        attempted = attempted_indices()
        skip = done | attempted
        spec = missing_ranges(skip, total)
        print(f"Captioned: {len(done)}  No-caption attempts: "
              f"{len(attempted - done)}  Remaining: {total - len(skip)} / {total}")
        if not spec:
            break
        before = (len(done), len(attempted))
        rc = run_ytdlp(args.showcase_url, args.password, spec)
        if rc != 0:
            print(f"yt-dlp exit {rc}", file=sys.stderr)
        convert_all()
        after = (len(downloaded_indices()), len(attempted_indices()))
        if not args.watch:
            break
        if after == before:
            stalled += 1
            if stalled >= 5:
                print("5 consecutive no-progress passes; exiting")
                break
        else:
            stalled = 0
        time.sleep(args.watch_sleep)

    convert_all()
    print(f"\nFinal: {len(downloaded_indices())} caption files, "
          f"{len(list(TXT_DIR.glob('*.txt')))} text files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
