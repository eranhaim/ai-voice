"""MiniMax Text-to-Speech and voice cloning API wrapper."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from io import BytesIO

import httpx

logger = logging.getLogger(__name__)

MINIMAX_API_BASE = "https://api.minimax.io"
MIN_CLONE_SECONDS = 10
MAX_CLONE_SECONDS = 5 * 60

# ISO 639-1 -> MiniMax language_boost label
LANGUAGE_BOOST = {
    "he": "Hebrew",
    "en": "English",
    "ar": "Arabic",
    "ru": "Russian",
    "fr": "French",
    "es": "Spanish",
}


def _api_key() -> str:
    key = os.getenv("MINIMAX_API_KEY", "")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY missing from env")
    return key


def _model() -> str:
    return os.getenv("MINIMAX_TTS_MODEL", "speech-2.6-hd")


def _headers(json_content: bool = False) -> dict[str, str]:
    h = {"Authorization": f"Bearer {_api_key()}"}
    if json_content:
        h["Content-Type"] = "application/json"
    return h


def _probe_duration(audio_bytes: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return 0.0
        return float(r.stdout.strip())
    except (ValueError, OSError):
        return 0.0
    finally:
        os.unlink(path)


def _to_mp3(audio_bytes: bytes) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-c:a", "libmp3lame", "-q:a", "2",
            "-f", "mp3", "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3 convert failed: {result.stderr.decode()[:200]}")
    return result.stdout


def prepare_clone_audio(samples: list[bytes], stitch_fn) -> bytes:
    """Concatenate samples and convert to MP3 for MiniMax clone upload."""
    if not samples:
        raise ValueError("no samples provided")
    if len(samples) == 1:
        combined = samples[0]
    else:
        combined = stitch_fn(samples)
    mp3 = _to_mp3(combined)
    duration = _probe_duration(mp3)
    if duration < MIN_CLONE_SECONDS:
        raise ValueError(
            f"clone audio too short ({duration:.1f}s, need {MIN_CLONE_SECONDS}s+)"
        )
    if duration > MAX_CLONE_SECONDS:
        mp3 = _trim_mp3(mp3, MAX_CLONE_SECONDS)
    if len(mp3) > 20 * 1024 * 1024:
        raise ValueError("clone audio exceeds 20 MB limit")
    return mp3


def _trim_mp3(mp3_bytes: bytes, max_seconds: float) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-t", str(max_seconds),
            "-c:a", "libmp3lame", "-q:a", "2",
            "-f", "mp3", "pipe:1",
        ],
        input=mp3_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning("ffmpeg trim failed, using untrimmed audio")
        return mp3_bytes
    return result.stdout


def upload_file(audio_bytes: bytes, purpose: str = "voice_clone", filename: str = "clone.mp3") -> int:
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{MINIMAX_API_BASE}/v1/files/upload",
            headers=_headers(),
            data={"purpose": purpose},
            files={"file": (filename, BytesIO(audio_bytes), "audio/mpeg")},
        )
        resp.raise_for_status()
        body = resp.json()
    file_id = (body.get("file") or {}).get("file_id")
    if not file_id:
        raise RuntimeError(f"MiniMax upload missing file_id: {body}")
    return int(file_id)


def clone_voice(
    audio_bytes: bytes,
    voice_id: str,
    language: str = "he",
    preview_text: str | None = None,
) -> str:
    """Upload audio and clone. Returns the custom voice_id."""
    file_id = upload_file(audio_bytes, purpose="voice_clone")
    lang_boost = LANGUAGE_BOOST.get(language, "auto")
    text = preview_text or (
        "שלום, זה מבחן קול. אני שמחה לדבר איתך."
        if language == "he"
        else "Hello, this is a voice test."
    )
    payload = {
        "file_id": file_id,
        "voice_id": voice_id,
        "text": text,
        "model": _model(),
        "language_boost": lang_boost,
        "need_noise_reduction": True,
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{MINIMAX_API_BASE}/v1/voice_clone",
            headers=_headers(json_content=True),
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
    base = body.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax clone failed: {base.get('status_msg', body)}")
    logger.info("MiniMax cloned voice %s (file_id=%s)", voice_id, file_id)
    return voice_id


def clone_from_samples(
    samples: list[bytes],
    voice_id: str,
    language: str,
    stitch_fn,
) -> str:
    mp3 = prepare_clone_audio(samples, stitch_fn)
    return clone_voice(mp3, voice_id, language=language)


def synthesize(
    text: str,
    voice_id: str,
    speed: float = 1.0,
    language: str = "he",
) -> bytes:
    """Generate MP3 bytes via MiniMax T2A v2."""
    lang_boost = LANGUAGE_BOOST.get(language, "auto")
    speed = max(0.5, min(2.0, float(speed)))
    payload = {
        "model": _model(),
        "text": text,
        "stream": False,
        "language_boost": lang_boost,
        "output_format": "hex",
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{MINIMAX_API_BASE}/v1/t2a_v2",
            headers=_headers(json_content=True),
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()

    base = body.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax TTS failed: {base.get('status_msg', body)}")

    data = body.get("data") or {}
    audio_hex = data.get("audio")
    if not audio_hex:
        raise RuntimeError(f"MiniMax TTS returned no audio: {body}")
    return bytes.fromhex(audio_hex)


def delete_voice(voice_id: str) -> None:
    payload = {"voice_type": "voice_cloning", "voice_id": voice_id}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{MINIMAX_API_BASE}/v1/delete_voice",
            headers=_headers(json_content=True),
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
    base = body.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax delete failed: {base.get('status_msg', body)}")
    logger.info("MiniMax deleted voice %s", voice_id)
