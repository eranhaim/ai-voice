"""Thin wrapper around the ElevenLabs Professional Voice Clone (PVC) API.

PVC is a multi-step pipeline:
    1. create voice (just allocates an id and metadata)
    2. upload audio samples
    3. fetch + submit a captcha recording to verify identity
    4. start training; ElevenLabs runs fine-tuning asynchronously (hours)
    5. poll voice.fine_tuning.state until it reaches "fine_tuned" or "failed"

The SDK exposes most of these directly. The one rough edge is captcha audio:
`client.voices.pvc.verification.captcha.get()` returns `None` in SDK 2.40 even
though the underlying HTTP endpoint streams back an MP3 of the phrase the user
must repeat. We bypass the SDK and call the endpoint with httpx for that.
"""

from __future__ import annotations

import logging
import os
from io import BytesIO
from typing import Any

import httpx
from elevenlabs.client import ElevenLabs

logger = logging.getLogger(__name__)

ELEVENLABS_API_BASE = "https://api.elevenlabs.io"

# Terminal/in-progress states reported by ElevenLabs fine-tuning. See
# FineTuningResponseModelStateValue in the SDK.
PVC_STATE_FINE_TUNED = "fine_tuned"
PVC_STATE_FAILED = "failed"
PVC_IN_PROGRESS_STATES = {"not_started", "queued", "fine_tuning", "delayed"}


def _client() -> ElevenLabs:
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing from env")
    return ElevenLabs(api_key=api_key)


def create_pvc_voice(name: str, language: str = "he", description: str | None = None) -> str:
    """Allocate an empty PVC voice slot. Returns the new voice_id."""
    c = _client()
    resp = c.voices.pvc.create(name=name, language=language, description=description)
    return resp.voice_id


def upload_pvc_samples(
    voice_id: str,
    audio_files: list[bytes],
    remove_background_noise: bool = True,
) -> list[str]:
    """Upload one or more audio samples to a PVC voice. Returns sample ids."""
    c = _client()
    files = []
    for i, data in enumerate(audio_files):
        buf = BytesIO(data)
        buf.name = f"sample_{i}.ogg"
        files.append(buf)
    samples = c.voices.pvc.samples.create(
        voice_id=voice_id,
        files=files,
        remove_background_noise=remove_background_noise,
    )
    return [s.sample_id for s in samples if getattr(s, "sample_id", None)]


def start_pvc_training(voice_id: str, model_id: str | None = None) -> None:
    """Kick off fine-tuning. Must be called only after captcha verification."""
    c = _client()
    c.voices.pvc.train(voice_id=voice_id, model_id=model_id)


def get_pvc_captcha(voice_id: str) -> tuple[bytes, str]:
    """Fetch the captcha the user must repeat for verification.

    Returns (content_bytes, content_type). ElevenLabs may return either an
    audio clip (MP3) or an image (PNG) depending on the voice/account.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing from env")
    resp = httpx.get(
        f"{ELEVENLABS_API_BASE}/v1/voices/pvc/{voice_id}/captcha",
        headers={"xi-api-key": api_key},
        timeout=30.0,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "application/octet-stream").lower()
    return resp.content, content_type


def get_pvc_captcha_audio(voice_id: str) -> bytes:
    """Legacy wrapper — returns raw bytes. Prefer get_pvc_captcha()."""
    content, _ = get_pvc_captcha(voice_id)
    return content


def submit_pvc_captcha(voice_id: str, recording: bytes) -> dict:
    """Submit the user's captcha recording. Returns the SDK response as dict."""
    c = _client()
    buf = BytesIO(recording)
    buf.name = "captcha.ogg"
    resp = c.voices.pvc.verification.captcha.verify(
        voice_id=voice_id,
        recording=buf,
    )
    return resp.model_dump() if hasattr(resp, "model_dump") else dict(resp or {})


def get_pvc_status(voice_id: str) -> dict[str, Any]:
    """Return a normalized status dict for a PVC voice.

    Keys returned:
        state                  - one of the ElevenLabs literal states, or "unknown"
        is_allowed_to_fine_tune
        progress               - float in [0,1] (max across models)
        verification_attempts_count
        max_verification_attempts
        message                - per-model status messages from ElevenLabs
    """
    c = _client()
    voice = c.voices.get(voice_id=voice_id)
    ft = getattr(voice, "fine_tuning", None)
    state_map = getattr(ft, "state", None) or {}
    progress_map = getattr(ft, "progress", None) or {}
    message_map = getattr(ft, "message", None) or {}

    # Pick the most-advanced state among trained models (caller doesn't care which).
    priority = ["failed", "fine_tuned", "fine_tuning", "queued", "delayed", "not_started"]
    state = "unknown"
    for cand in priority:
        if cand in state_map.values():
            state = cand
            break

    try:
        progress = max((float(v) for v in progress_map.values() if v is not None), default=0.0)
    except (TypeError, ValueError):
        progress = 0.0

    return {
        "state": state,
        "raw_state_map": dict(state_map),
        "is_allowed_to_fine_tune": getattr(ft, "is_allowed_to_fine_tune", False),
        "progress": progress,
        "verification_attempts_count": getattr(ft, "verification_attempts_count", 0),
        "max_verification_attempts": getattr(ft, "max_verification_attempts", 0),
        "message": dict(message_map),
    }


def delete_pvc_voice(voice_id: str) -> None:
    """Delete a PVC voice from the ElevenLabs account (frees the slot)."""
    try:
        _client().voices.delete(voice_id=voice_id)
    except Exception:
        logger.exception("Failed to delete PVC voice %s", voice_id)
