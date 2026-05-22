"""PlayAI Text-to-Speech API wrapper (https://play.ai)."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

PLAYAI_API_BASE = "https://api.play.ai"

# ISO 639-1 -> PlayAI language param
LANGUAGE_MAP = {
    "he": "hebrew",
    "en": "english",
    "ar": "arabic",
    "ru": "russian",
    "fr": "french",
    "es": "spanish",
}


def _api_key() -> str:
    key = os.getenv("PLAYAI_API_KEY", "")
    if not key:
        raise RuntimeError("PLAYAI_API_KEY missing from env")
    return key


def _user_id() -> str:
    uid = os.getenv("PLAYAI_USER_ID", "")
    if not uid:
        raise RuntimeError("PLAYAI_USER_ID missing from env")
    return uid


def _model() -> str:
    return os.getenv("PLAYAI_TTS_MODEL", "PlayDialog")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "X-USER-ID": _user_id(),
    }


def synthesize(
    text: str,
    voice_id: str,
    speed: float = 1.0,
    language: str = "he",
) -> bytes:
    """Generate MP3 bytes via PlayAI TTS stream endpoint."""
    lang = LANGUAGE_MAP.get(language, "hebrew" if language == "he" else language)
    speed = max(0.5, min(2.0, float(speed)))
    payload = {
        "model": _model(),
        "text": text,
        "voice": voice_id,
        "outputFormat": "mp3",
        "language": lang,
        "speed": speed,
    }
    logger.info(
        "PlayAI TTS model=%s language=%s voice=%s chars=%d",
        _model(), lang, voice_id[:60], len(text),
    )
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{PLAYAI_API_BASE}/api/v1/tts/stream",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        audio = resp.content
    if not audio:
        raise RuntimeError("PlayAI TTS returned empty audio")
    return audio
