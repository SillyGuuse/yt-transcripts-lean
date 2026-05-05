"""
GPU Whisper consumer for the audio prefetched by download_audio.py.

Looks at every video that needs Whisper (no .vtt and no .txt yet).
For each, if an .mp3 is already in audio/, transcribe with faster-whisper
and write a .txt next to the captioned ones in txt/. If no audio yet,
skip — download_audio.py is still producing it.

Designed to be called repeatedly by a supervisor: each run drains
whatever audio is ready, then exits. The supervisor sleeps a bit and
re-runs until the to-do list is empty.

Usage:
    python transcribe_whisper.py                        # cuda + medium + float16
    python transcribe_whisper.py --device cpu --model small --compute-type int8
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SUBS_DIR = ROOT / "subs"
TXT_DIR = ROOT / "txt"
AUDIO_DIR = ROOT / "audio"
FAIL_DIR = ROOT / "whisper_failed"
MANIFEST = ROOT / "manifest.tsv"

# Windows cp1252 console crashes on non-Latin1 chars in titles. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def has_caption(idx: int) -> bool:
    return any(SUBS_DIR.glob(f"{idx:03d} - *.vtt"))


def has_text(idx: int) -> bool:
    return any(TXT_DIR.glob(f"{idx:03d} - *.txt"))


def find_audio(idx: int) -> Path | None:
    matches = list(AUDIO_DIR.glob(f"{idx:03d} - *.mp3"))
    return matches[0] if matches else None


def has_failed(idx: int) -> bool:
    return (FAIL_DIR / f"{idx:03d}.failed").exists()


def load_manifest() -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    if not MANIFEST.exists():
        raise SystemExit("manifest.tsv missing — run download_audio.py first to build it")
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1], parts[2] if len(parts) > 2 else ""))
    return rows


def transcribe(audio: Path, model) -> str:
    segments, _info = model.transcribe(str(audio), beam_size=1, vad_filter=True)
    pieces: list[str] = []
    last = ""
    for seg in segments:
        line = seg.text.strip()
        if line and line != last:
            pieces.append(line)
            last = line
    return "\n".join(pieces) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="medium",
                   help="tiny/base/small/medium/large-v3")
    p.add_argument("--device", default="cuda", help="cuda/cpu/auto")
    p.add_argument("--compute-type", default="float16",
                   help="float16 (GPU), int8_float16 (GPU low VRAM), int8 (CPU)")
    p.add_argument("--keep-audio", action="store_true",
                   help="don't delete .mp3 after transcription")
    args = p.parse_args()

    FAIL_DIR.mkdir(exist_ok=True)
    TXT_DIR.mkdir(exist_ok=True)

    todo = [
        (idx, vid, title) for idx, vid, title in load_manifest()
        if not has_caption(idx) and not has_text(idx) and not has_failed(idx)
    ]
    if not todo:
        print("Whisper-needed: 0")
        print("Nothing to transcribe.")
        return 0

    from faster_whisper import WhisperModel
    print(f"Loading faster-whisper model={args.model} device={args.device} "
          f"compute={args.compute_type} ...")
    try:
        model = WhisperModel(args.model, device=args.device,
                             compute_type=args.compute_type)
    except Exception as e:
        if args.device != "cpu":
            print(f"  GPU load failed ({e}); falling back to CPU/int8")
            model = WhisperModel(args.model, device="cpu", compute_type="int8")
        else:
            raise

    skipped = 0
    for n, (idx, vid, title) in enumerate(todo, 1):
        try:
            audio = find_audio(idx)
            if audio is None:
                # download_audio.py is still producing this one
                skipped += 1
                continue
            print(f"\n=== [{n}/{len(todo)}] #{idx} {title[:60]} ===")
            try:
                text = transcribe(audio, model)
            except Exception as e:
                print(f"  ! transcription error: {e}")
                (FAIL_DIR / f"{idx:03d}.failed").write_text(
                    f"transcription error: {e}\n", encoding="utf-8")
                continue
            out = TXT_DIR / f"{audio.stem}.txt"
            out.write_text(text, encoding="utf-8")
            print(f"  -> {out.name}  ({len(text):,} chars)")
            if not args.keep_audio:
                try:
                    audio.unlink()
                except OSError:
                    pass
        except Exception as e:
            print(f"  ! unhandled error on idx={idx}: {e!r}")
            (FAIL_DIR / f"{idx:03d}.failed").write_text(
                f"unhandled: {e!r}\n", encoding="utf-8")
            continue

    print(f"\nPass complete. Skipped (no audio yet): {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
