"""Validation for voice-reference audio."""

from __future__ import annotations

import audioop
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

MIN_SAMPLE_SECONDS = 5
MAX_SAMPLE_SECONDS = 10 * 60
MAX_SAMPLE_BYTES = 25 * 1024 * 1024
MIN_RMS = 100
MAX_CLIPPED_RATIO = 0.02


class ReferenceAudioError(ValueError):
    """Raised when an enrollment reference is unsuitable for cloning."""


@dataclass(frozen=True)
class ReferenceAudioInfo:
    duration_seconds: float
    codec: str
    rms: int
    clipped_ratio: float


def validate_reference_audio(audio_bytes: bytes) -> ReferenceAudioInfo:
    """Reject malformed, silent, clipped, or impractically long references."""
    if not audio_bytes:
        raise ReferenceAudioError("Audio file is empty.")
    if len(audio_bytes) > MAX_SAMPLE_BYTES:
        raise ReferenceAudioError("Audio file exceeds the 25 MB limit.")

    path = _write_temp_audio(audio_bytes)
    try:
        metadata = _probe_audio(path)
        duration = float(metadata["format"]["duration"])
        if not MIN_SAMPLE_SECONDS <= duration <= MAX_SAMPLE_SECONDS:
            raise ReferenceAudioError(
                f"Each reference must be {MIN_SAMPLE_SECONDS} seconds to "
                f"{MAX_SAMPLE_SECONDS // 60} minutes long."
            )

        streams = metadata.get("streams", [])
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if not audio_stream or not audio_stream.get("codec_name"):
            raise ReferenceAudioError("File does not contain a decodable audio stream.")

        pcm = _decode_mono_pcm(path)
        if not pcm:
            raise ReferenceAudioError("Audio could not be decoded.")
        rms = audioop.rms(pcm, 2)
        if rms < MIN_RMS:
            raise ReferenceAudioError("Reference is too quiet or contains too much silence.")

        samples = len(pcm) // 2
        clipped = sum(
            1 for offset in range(0, len(pcm), 2)
            if abs(int.from_bytes(pcm[offset:offset + 2], "little", signed=True)) >= 32700
        )
        clipped_ratio = clipped / samples if samples else 1.0
        if clipped_ratio > MAX_CLIPPED_RATIO:
            raise ReferenceAudioError("Reference is heavily clipped; upload a cleaner recording.")

        return ReferenceAudioInfo(
            duration_seconds=round(duration, 2),
            codec=audio_stream["codec_name"],
            rms=rms,
            clipped_ratio=round(clipped_ratio, 5),
        )
    finally:
        os.unlink(path)


def _write_temp_audio(audio_bytes: bytes) -> str:
    descriptor, path = tempfile.mkstemp(suffix=".audio")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(audio_bytes)
    except Exception:
        os.unlink(path)
        raise
    return path


def _probe_audio(path: str) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_name,codec_type",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ReferenceAudioError("Audio format is not supported.")
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise ReferenceAudioError("Audio metadata could not be read.") from error


def _decode_mono_pcm(path: str) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ReferenceAudioError("Audio could not be decoded.")
    return result.stdout
