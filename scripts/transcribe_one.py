#!/usr/bin/env python3
"""Transcribe a single podcast audio file in an isolated subprocess.

Invoked by src/crawlers/podcasts.py so each Whisper inference runs in its
own OS process — when this process exits, macOS reclaims every byte of
unified memory the model and intermediate tensors occupied.

Without this isolation, sequential mlx-whisper.transcribe() calls inside
one Python process do not release MLX/Metal buffers between episodes, and
memory grows monotonically until the kernel kills the parent (May 2026
OOM incident).

Usage:
    transcribe_one.py <audio_url> <output_txt_path>

Exit codes:
    0 — transcript written to <output_txt_path>
    1 — failure (message on stderr)
    2 — bad CLI args
"""
import os
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen, Request

WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx-4bit"


def transcribe(audio_url: str, output_path: str) -> int:
    import mlx_whisper

    print("    Downloading audio…", flush=True)
    req = Request(audio_url, headers={'User-Agent': 'Mozilla/5.0 IIA-Podcast-Crawler'})

    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        tmp_path = f.name
        with urlopen(req, timeout=120) as r:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

    size_mb = os.path.getsize(tmp_path) / 1024 / 1024
    print(f"    Downloaded {size_mb:.1f} MB — transcribing…", flush=True)

    try:
        result = mlx_whisper.transcribe(
            tmp_path,
            path_or_hf_repo=WHISPER_MODEL,
            language="zh",
            verbose=False,
        )
        transcript = (result.get('text') or '').strip()
        Path(output_path).write_text(transcript, encoding='utf-8')
        print(f"    Transcript: {len(transcript)} chars", flush=True)
        return 0
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <audio_url> <output_txt_path>", file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(transcribe(sys.argv[1], sys.argv[2]))
    except Exception as exc:
        print(f"    Transcription error: {exc}", file=sys.stderr)
        sys.exit(1)
