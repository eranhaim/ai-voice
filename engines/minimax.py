"""MiniMax Speech (speech-2.8-hd): Hebrew TTS with instant voice cloning.

Docs: https://platform.minimax.io/docs/api-reference/speech-t2a-http
Env:  MINIMAX_API_KEY (required), MINIMAX_TTS_MODEL (default speech-2.8-hd)

MiniMax deletes cloned voices that go unused for 7 days. `ensure_voice` in the
bot re-clones from the stored reference audio when that happens.
"""
from __future__ import annotations

import logging
import os
from io import BytesIO

import httpx

from engines import audio

logger = logging.getLogger(__name__)

API_BASE = "https://api.minimax.io"
MIN_CLONE_SECONDS = 10
MAX_CLONE_SECONDS = 5 * 60

LANGUAGE_BOOST = {
    "he": "Hebrew", "en": "English", "ar": "Arabic",
    "ru": "Russian", "fr": "French", "es": "Spanish",
}


def is_enabled() -> bool:
    return bool(os.getenv("MINIMAX_API_KEY"))


def model() -> str:
    return os.getenv("MINIMAX_TTS_MODEL", "speech-2.8-hd")


def _headers(json_body: bool = False) -> dict[str, str]:
    key = os.getenv("MINIMAX_API_KEY", "")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY missing from env")
    h = {"Authorization": f"Bearer {key}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _check(body: dict, what: str) -> None:
    base = body.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax {what} failed: {base.get('status_msg', body)}")


def voice_id_for(voice_doc_id: str) -> str:
    # Custom ids: 8-256 chars, must start with a letter, letters/digits only here.
    return f"voice{voice_doc_id}"


def prepare_clone_audio(samples: list[bytes]) -> bytes:
    """Join samples into one MP3 of 10 s .. 5 min, looping very short audio."""
    joined = audio.concat(samples)
    dur = audio.duration_seconds(joined)
    if dur < MIN_CLONE_SECONDS:
        loops = int(MIN_CLONE_SECONDS / max(dur, 0.5)) + 1
        joined = audio.concat([joined] * loops)
    return audio.to_mp3(joined, max_seconds=MAX_CLONE_SECONDS)


def _upload(mp3: bytes) -> int:
    with httpx.Client(timeout=120.0) as c:
        r = c.post(
            f"{API_BASE}/v1/files/upload",
            headers=_headers(),
            data={"purpose": "voice_clone"},
            files={"file": ("clone.mp3", BytesIO(mp3), "audio/mpeg")},
        )
        r.raise_for_status()
        body = r.json()
    _check(body, "upload")
    file_id = (body.get("file") or {}).get("file_id")
    if not file_id:
        raise RuntimeError(f"MiniMax upload returned no file_id: {body}")
    return int(file_id)


def clone(samples: list[bytes], voice_id: str, language: str = "he") -> str:
    """Create (or recreate) the MiniMax clone `voice_id`. Returns the voice_id."""
    file_id = _upload(prepare_clone_audio(samples))
    payload = {
        "file_id": file_id,
        "voice_id": voice_id,
        "model": model(),
        "text": "שלום, זה מבחן קול. אני שמחה לדבר איתך." if language == "he" else "Hello, this is a voice test.",
        "language_boost": LANGUAGE_BOOST.get(language, "auto"),
        "need_noise_reduction": True,
        "need_volume_normalization": True,
    }
    with httpx.Client(timeout=180.0) as c:
        r = c.post(f"{API_BASE}/v1/voice_clone", headers=_headers(True), json=payload)
        r.raise_for_status()
        body = r.json()
    _check(body, "voice clone")
    logger.info("MiniMax cloned %s (file_id=%s)", voice_id, file_id)
    return voice_id


def synthesize(
    text: str,
    voice_id: str,
    speed: float = 1.0,
    language: str = "he",
    emotion: str | None = None,
) -> bytes:
    """Hebrew-capable TTS. Returns MP3 bytes."""
    voice_setting = {
        "voice_id": voice_id,
        "speed": max(0.5, min(2.0, float(speed))),
        "vol": 1.0,
        "pitch": 0,
    }
    if emotion:
        voice_setting["emotion"] = emotion
    payload = {
        "model": model(),
        "text": text,
        "stream": False,
        "language_boost": LANGUAGE_BOOST.get(language, "auto"),
        "output_format": "hex",
        "voice_setting": voice_setting,
        "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3", "channel": 1},
    }
    with httpx.Client(timeout=120.0) as c:
        r = c.post(f"{API_BASE}/v1/t2a_v2", headers=_headers(True), json=payload)
        r.raise_for_status()
        body = r.json()
    _check(body, "TTS")
    hex_audio = (body.get("data") or {}).get("audio")
    if not hex_audio:
        raise RuntimeError(f"MiniMax TTS returned no audio: {str(body)[:300]}")
    return bytes.fromhex(hex_audio)


def is_missing_voice_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "voice" in msg and ("not exist" in msg or "not found" in msg or "invalid" in msg)


def delete_voice(voice_id: str) -> None:
    with httpx.Client(timeout=30.0) as c:
        r = c.post(
            f"{API_BASE}/v1/delete_voice",
            headers=_headers(True),
            json={"voice_type": "voice_cloning", "voice_id": voice_id},
        )
        r.raise_for_status()
        _check(r.json(), "delete")
