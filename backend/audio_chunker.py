"""
audio_chunker.py
=================
Detects audio duration and splits long recordings into sequential,
slightly-overlapping chunks so they can be transcribed one at a time
instead of sending 1-2+ hours of audio into a single Whisper call.

Short audio (below SHORT_AUDIO_THRESHOLD_SECONDS) is left completely
untouched and returned as a single "chunk" that points directly at the
original file — no re-encoding, no splitting, same behavior as before.

Chunks are written as 16kHz mono WAV files. This is not "extra" re-encoding
overhead: Whisper internally decodes every input to 16kHz mono anyway, so
pre-splitting into that format costs nothing extra at transcription time
and keeps each chunk file small on disk.
"""

import math
import subprocess
from pathlib import Path
from typing import Union

# Below this duration, don't bother chunking at all.
SHORT_AUDIO_THRESHOLD_SECONDS = 8 * 60  # 8 minutes


def get_duration_seconds(audio_path: Union[str, Path]) -> float:
    """Use ffprobe to read the duration of an audio file, in seconds."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"ffprobe could not read duration: {proc.stderr.strip()}")
    return float(out)


def split_into_chunks(
    audio_path: Union[str, Path],
    chunks_dir: Union[str, Path],
    chunk_seconds: int = 420,
    overlap_seconds: int = 20,
) -> list[dict]:
    """
    Split `audio_path` into chronological, overlapping chunks written to
    `chunks_dir`.

    Returns a list of dicts (chronological order):
        {"index": 1, "path": "...", "start": 0.0, "end": 420.0}

    Idempotent: if a chunk file already exists on disk (e.g. from a
    previously-interrupted job), it is reused rather than re-created. This
    is what makes resuming a crashed job cheap — we don't even need to
    re-run ffmpeg for chunks that already exist.

    If the audio is short enough, a single chunk pointing directly at the
    original file is returned (no re-encoding at all).
    """
    chunks_dir = Path(chunks_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    duration = get_duration_seconds(audio_path)

    if duration <= SHORT_AUDIO_THRESHOLD_SECONDS:
        return [{"index": 1, "path": str(audio_path), "start": 0.0, "end": duration}]

    n_chunks = max(1, math.ceil(duration / chunk_seconds))
    infos: list[dict] = []

    for i in range(n_chunks):
        start = max(0.0, i * chunk_seconds - (overlap_seconds if i > 0 else 0))
        end = min(duration, (i + 1) * chunk_seconds)
        dur = end - start
        out_path = chunks_dir / f"{i + 1:03d}.wav"

        if not out_path.exists() or out_path.stat().st_size == 0:
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-i", str(audio_path),
                "-t", f"{dur:.3f}",
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(out_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(
                    f"ffmpeg failed while splitting chunk {i + 1}: {proc.stderr[-1500:]}"
                )

        infos.append({"index": i + 1, "path": str(out_path), "start": start, "end": end})

    return infos
