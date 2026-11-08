"""Client for the Modal `voice-lab` app (Applio RVC + Seed-VC).

Env: MODAL_TOKEN_ID, MODAL_TOKEN_SECRET. The modal SDK is imported lazily so
the bot still starts without it.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

APP_NAME = "voice-lab"


def is_enabled() -> bool:
    return bool(os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"))


def _fn(name: str):
    import modal
    return modal.Function.from_name(APP_NAME, name)


def spawn_applio_training(voice_doc_id: str, samples: list[bytes], epochs: int | None = None) -> str:
    """Start Applio training in the background. Returns the Modal call id."""
    epochs = epochs or int(os.getenv("APPLIO_EPOCHS", "250"))
    call = _fn("applio_train").spawn(voice_doc_id, samples, epochs)
    cid = getattr(call, "object_id", "") or str(call)
    logger.info("Applio training spawned for %s (%d samples, %d epochs) call=%s",
                voice_doc_id, len(samples), epochs, cid)
    return cid


def applio_status(voice_doc_id: str) -> dict:
    return _fn("applio_status").remote(voice_doc_id)


def applio_convert(voice_doc_id: str, audio: bytes, pitch: int = 0) -> bytes:
    import modal
    converter = modal.Cls.from_name(APP_NAME, "ApplioConverter")()
    return converter.convert.remote(
        voice_doc_id, audio, pitch,
        float(os.getenv("APPLIO_INDEX_RATE", "0.75")),
        float(os.getenv("APPLIO_PROTECT", "0.33")),
    )


def applio_delete(voice_doc_id: str) -> None:
    _fn("applio_delete").remote(voice_doc_id)


def seedvc_convert(source: bytes, reference: bytes, semitones: int = 0) -> bytes:
    return _fn("seedvc_convert").remote(
        source, reference,
        int(os.getenv("SEEDVC_DIFFUSION_STEPS", "30")),
        float(os.getenv("SEEDVC_CFG_RATE", "0.7")),
        int(semitones),
    )
