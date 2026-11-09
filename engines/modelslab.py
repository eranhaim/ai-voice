"""ModelsLab voice APIs: zero-shot voice changer (STS) and cloned Hebrew TTS.

Docs: https://docs.modelslab.com/voice-cloning/overview
Env:  MODELSLAB_API_KEY

Both endpoints take audio as URLs, so the bot passes short-lived presigned
object-storage links. Inputs are capped at 30 s by ModelsLab, so the bot trims
references; long source recordings are truncated to 30 s for this engine.
Requests may come back as `processing`; we poll the fetch endpoint.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://modelslab.com/api/v6/voice"
MAX_INPUT_SECONDS = 30
POLL_SECONDS = 4
POLL_TIMEOUT = 240

LANGUAGE = {
    "he": "hebrew", "en": "english", "ar": "arabic",
    "ru": "russian", "fr": "french", "es": "spanish",
}


def is_enabled() -> bool:
    return bool(os.getenv("MODELSLAB_API_KEY"))


def _key() -> str:
    key = os.getenv("MODELSLAB_API_KEY", "")
    if not key:
        raise RuntimeError("MODELSLAB_API_KEY missing from env")
    return key


def _download(url: str) -> bytes:
    r = httpx.get(url, timeout=120.0, follow_redirects=True)
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("ModelsLab output file was empty")
    return r.content


def _result_audio(body: dict, client: httpx.Client) -> bytes:
    deadline = time.monotonic() + POLL_TIMEOUT
    # Fetch responses don't repeat the id, so remember where to poll.
    fetch_url = body.get("fetch_result") or f"{API_BASE}/fetch/{body.get('id')}"
    while True:
        status = body.get("status")
        if status == "success":
            outputs = body.get("output") or body.get("proxy_links") or []
            if not outputs:
                raise RuntimeError(f"ModelsLab returned no output: {str(body)[:300]}")
            return _download(outputs[0])
        if status not in ("processing", "queued"):
            raise RuntimeError(f"ModelsLab error: {body.get('message') or body.get('messege') or str(body)[:300]}")
        if time.monotonic() > deadline:
            raise TimeoutError("ModelsLab did not finish in time")
        time.sleep(min(POLL_SECONDS, max(1, int(body.get("eta") or POLL_SECONDS))))
        r = client.post(fetch_url, json={"key": _key()})
        r.raise_for_status()
        body = r.json()


def _post(path: str, payload: dict) -> bytes:
    with httpx.Client(timeout=120.0) as c:
        r = c.post(f"{API_BASE}/{path}", json={"key": _key(), **payload})
        r.raise_for_status()
        return _result_audio(r.json(), c)


def voice_to_voice(source_url: str, target_url: str) -> bytes:
    """Convert the speech at `source_url` into the voice at `target_url`."""
    return _post("voice_to_voice", {
        "model_id": "voice-changer",
        "init_audio": source_url,
        "target_audio": target_url,
        "base64": False,
        "temp": True,
    })


def text_to_speech(text: str, reference_url: str, language: str = "he") -> bytes:
    """Speak `text` in the voice of the reference clip (4-30 s)."""
    return _post("text_to_audio", {
        "model_id": "voice-cloning",
        "init_audio": reference_url,
        "prompt": text,
        "language": LANGUAGE.get(language, language),
        "base64": False,
        "temp": True,
    })
