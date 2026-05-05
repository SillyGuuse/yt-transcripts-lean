"""
Auto-restarts transcribe_whisper.py until everything is transcribed.
The transcription script does one pass and exits; we re-run it so it picks
up audio that download_audio.py has produced in the meantime.

Stops when transcribe_whisper reports "Whisper-needed: 0", or after
MAX_RESTARTS_NO_PROGRESS consecutive zero-progress restarts (true wedge).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
TXT_DIR = ROOT / "txt"
FAIL_DIR = ROOT / "whisper_failed"
MAX_RESTARTS_NO_PROGRESS = 30


def progress_signature() -> tuple[int, int]:
    return (
        len(list(TXT_DIR.glob("*.txt"))) if TXT_DIR.exists() else 0,
        len(list(FAIL_DIR.glob("*.failed"))) if FAIL_DIR.exists() else 0,
    )


def main(extra_args: list[str]) -> int:
    no_progress = 0
    while True:
        before = progress_signature()
        print(f"[supervisor] starting transcribe_whisper "
              f"(txt={before[0]}, failed={before[1]})", flush=True)
        try:
            rc = subprocess.call(
                [sys.executable, "transcribe_whisper.py", *extra_args], cwd=ROOT)
        except KeyboardInterrupt:
            return 130

        after = progress_signature()
        delta = (after[0] - before[0], after[1] - before[1])
        print(f"[supervisor] child exit rc={rc}  +txt={delta[0]} +failed={delta[1]}",
              flush=True)

        try:
            out = subprocess.check_output(
                [sys.executable, "transcribe_whisper.py", *extra_args],
                cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                timeout=120,
            )
        except Exception as e:
            print(f"[supervisor] dry-run check failed: {e}")
            out = ""
        if "Nothing to transcribe" in out or "Whisper-needed: 0" in out:
            print("[supervisor] done")
            return 0

        if delta == (0, 0):
            no_progress += 1
            if no_progress >= MAX_RESTARTS_NO_PROGRESS:
                print(f"[supervisor] {no_progress} consecutive zero-progress restarts; giving up")
                return 1
        else:
            no_progress = 0
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
