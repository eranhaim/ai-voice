"""ffmpeg helpers shared by the voice engines. All functions take and return bytes."""
from __future__ import annotations

import os
import subprocess
import tempfile


def _run(args: list[str], src: bytes) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".audio")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(src)
        r = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", path, *args, "pipe:1"],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr.decode(errors='replace')[:300]}")
        return r.stdout
    finally:
        os.unlink(path)


def duration_seconds(audio: bytes) -> float:
    fd, path = tempfile.mkstemp(suffix=".audio")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True,
        )
        for line in r.stdout.split():
            if line and line != "N/A":
                return float(line)
        return 0.0
    except (ValueError, OSError):
        return 0.0
    finally:
        os.unlink(path)


def to_mp3(audio: bytes, max_seconds: float | None = None) -> bytes:
    args = ["-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k"]
    if max_seconds:
        args = ["-t", str(max_seconds), *args]
    return _run([*args, "-f", "mp3"], audio)


def to_wav(audio: bytes, sample_rate: int = 44100) -> bytes:
    return _run(["-ac", "1", "-ar", str(sample_rate), "-f", "wav"], audio)


def concat(clips: list[bytes]) -> bytes:
    """Join clips of any format into one MP3."""
    if len(clips) == 1:
        return to_mp3(clips[0])
    tmp = tempfile.mkdtemp(prefix="concat_")
    try:
        paths = []
        for i, clip in enumerate(clips):
            p = os.path.join(tmp, f"{i}.wav")
            with open(p, "wb") as f:
                f.write(to_wav(clip))
            paths.append(p)
        listing = os.path.join(tmp, "list.txt")
        with open(listing, "w") as f:
            f.writelines(f"file '{p}'\n" for p in paths)
        r = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
             "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", "pipe:1"],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {r.stderr.decode(errors='replace')[:300]}")
        return r.stdout
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def reference_clip(samples: list[bytes], seconds: float = 28.0) -> bytes:
    """A single clean reference clip for zero-shot engines (Seed-VC, ModelsLab).

    Both cap the reference at ~25-30 s, so we join the samples and keep the start.
    """
    return to_mp3(concat(samples), max_seconds=seconds)
