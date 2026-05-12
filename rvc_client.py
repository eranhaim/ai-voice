"""Thin wrapper around the Modal `rvc-voice` app.

The bot uses this module instead of touching the modal SDK directly so we can
swap providers later without rewriting bot.py.

The Modal SDK is imported lazily so the bot still works if Modal isn't
configured (Casual-only deployments).
"""
import logging
import os

logger = logging.getLogger(__name__)

APP_NAME = "rvc-voice"


def is_enabled() -> bool:
    return bool(os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"))


def _train_fn():
    import modal
    return modal.Function.from_name(APP_NAME, "train_voice")


def _convert_fn():
    import modal
    return modal.Function.from_name(APP_NAME, "convert")


def spawn_training(voice_id: str, sample_urls: list[str], total_epoch: int = 300) -> str | None:
    """Kick off RVC training in the background. Returns a Modal call id or None.

    Status updates land directly in MongoDB via the Modal function, so we don't
    need to poll the call. We still return the id for debugging.
    """
    if not is_enabled():
        raise RuntimeError("Modal not configured (MODAL_TOKEN_ID/SECRET missing)")
    try:
        call = _train_fn().spawn(voice_id, sample_urls, total_epoch)
        cid = getattr(call, "object_id", None) or str(call)
        logger.info("RVC training spawned voice_id=%s call=%s", voice_id, cid)
        return cid
    except Exception:
        logger.exception("Failed to spawn RVC training")
        raise


def convert_audio(
    voice_id: str,
    audio_bytes: bytes,
    f0_up_key: int = 0,
    index_rate: float = 0.95,
    rms_mix_rate: float = 0.05,
    protect: float = 0.33,
    filter_radius: int = 3,
) -> bytes:
    """Synchronously convert audio to the target RVC voice. Returns mp3 bytes."""
    if not is_enabled():
        raise RuntimeError("Modal not configured (MODAL_TOKEN_ID/SECRET missing)")
    fn = _convert_fn()
    result = fn.remote(
        voice_id, audio_bytes, int(f0_up_key),
        float(index_rate), float(rms_mix_rate),
        float(protect), int(filter_radius),
    )
    if not result:
        raise RuntimeError("RVC convert returned empty audio")
    return result
