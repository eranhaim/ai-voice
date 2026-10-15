"""Instant Voice Clone (IVC) creation, called over HTTP rather than the SDK.

`client.voices.ivc.create()` is rejected by the API with
`{"status": "invalid_labels", "message": "Labels must be serialized dictionary
object."}` on SDK 2.71 and 2.73 — verified against both, with and without an
explicit `labels` argument. The SDK sends the field in a shape the endpoint will
not accept, so every clone attempt fails before the audio is even looked at.
`POST /v1/voices/add` accepts the same request and works, so this module calls
it directly. `requirements.txt` does not pin the SDK, so a rebuild can
reintroduce the break; going straight to the endpoint keeps voice creation —
the whole product — off that moving part.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_API_BASE = "https://api.elevenlabs.io"

# Cloning uploads minutes of audio, so allow well over the default timeout.
CLONE_TIMEOUT_SECONDS = 300.0


def create_ivc_voice(
    name: str,
    audio_files: list[bytes],
    remove_background_noise: bool = True,
) -> str:
    """Clone a voice from reference audio. Returns the new voice_id."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing from env")

    files = [
        ("files", (f"sample_{i}.mp3", data, "application/octet-stream"))
        for i, data in enumerate(audio_files)
    ]
    response = httpx.post(
        f"{ELEVENLABS_API_BASE}/v1/voices/add",
        headers={"xi-api-key": api_key},
        data={
            "name": name,
            "remove_background_noise": str(remove_background_noise).lower(),
        },
        files=files,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    voice_id = response.json()["voice_id"]
    logger.info("IVC clone created from %d samples", len(audio_files))
    return voice_id
