"""Runs the alternative voice engines for a voice, and compares them side by side.

The bot keeps ElevenLabs as the baseline and passes its own ElevenLabs calls in
as callables, so this module never imports bot.py.

Per-voice state (MongoDB `voices` / `system_voices` docs):
- reference_audio_url: <=28 s MP3 of the target voice (Seed-VC, ModelsLab,
  and MiniMax re-clones)
- minimax_voice_id:    MiniMax clone id
- applio_status:       training | ready | failed (Applio RVC model on Modal)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from db import set_voice_fields
from engines import STS_ENGINES, TTS_ENGINES, audio, gpu, minimax, modelslab
import s3

logger = logging.getLogger(__name__)

ENGINE_TIMEOUT_SECONDS = 300
_NIKUD = re.compile("[֑-ׇ]")
_AUDIO_TAG = re.compile(r"\[[^\]]*\]")


class EngineUnavailable(Exception):
    """The engine can't run for this voice (missing key, model, or reference)."""


@dataclass
class EngineResult:
    engine: str
    label: str
    audio: bytes | None
    seconds: float
    error: str | None = None


ElevenTTS = Callable[[], bytes]
ElevenSTS = Callable[[], bytes]
PreviewFetcher = Callable[[str], bytes | None]


def plain_text(text: str) -> str:
    """Text for engines other than ElevenLabs: no nikud, no [audio tags]."""
    return re.sub(r"\s{2,}", " ", _AUDIO_TAG.sub("", _NIKUD.sub("", text))).strip()


# ── voice preparation ────────────────────────────────────────────────────────

async def prepare_voice(voice_doc_id: str, samples: list[bytes], language: str = "he") -> dict[str, str]:
    """Set a freshly cloned voice up for every engine. Each step is best-effort.

    Returns {step: "ok" | "skipped: why" | "failed: why"} for the bot to report.
    """
    report: dict[str, str] = {}

    try:
        ref = await asyncio.to_thread(audio.reference_clip, samples)
        url = await asyncio.to_thread(s3.upload_object, f"voices/reference/{voice_doc_id}.mp3", ref)
        await set_voice_fields(voice_doc_id, reference_audio_url=url)
        report["reference"] = "ok"
    except Exception as exc:
        logger.exception("Reference clip failed for %s", voice_doc_id)
        report["reference"] = f"failed: {exc}"

    if minimax.is_enabled():
        try:
            vid = await asyncio.to_thread(
                minimax.clone, samples, minimax.voice_id_for(voice_doc_id), language,
            )
            await set_voice_fields(voice_doc_id, minimax_voice_id=vid)
            report["minimax"] = "ok"
        except Exception as exc:
            logger.exception("MiniMax clone failed for %s", voice_doc_id)
            report["minimax"] = f"failed: {exc}"
    else:
        report["minimax"] = "skipped: MINIMAX_API_KEY not set"

    if gpu.is_enabled():
        try:
            await asyncio.to_thread(gpu.spawn_applio_training, voice_doc_id, samples)
            await set_voice_fields(
                voice_doc_id, applio_status="training", applio_notified=False, applio_error=None,
            )
            report["applio"] = "ok"
        except Exception as exc:
            logger.exception("Applio training spawn failed for %s", voice_doc_id)
            report["applio"] = f"failed: {exc}"
    else:
        report["applio"] = "skipped: Modal not configured"

    return report


async def load_samples(voice: dict) -> list[bytes]:
    """Download a custom voice's stored samples (skips any that are unreachable)."""
    samples = []
    for url in voice.get("sample_urls") or []:
        try:
            samples.append(await asyncio.to_thread(s3.download_sample, url))
        except Exception:
            logger.warning("Sample unreachable: %s", url)
    return samples


async def get_reference(voice: dict, preview_fetcher: PreviewFetcher) -> bytes:
    """Reference audio of the target voice for zero-shot engines."""
    url = voice.get("reference_audio_url")
    if url:
        try:
            return await asyncio.to_thread(s3.download_sample, url)
        except Exception:
            logger.warning("Stored reference unreachable for %s", voice["id"])

    samples = await load_samples(voice)
    if not samples:
        # System voices (and voices whose samples are gone): use the ElevenLabs preview.
        preview = await asyncio.to_thread(preview_fetcher, voice["elevenlabs_voice_id"])
        if not preview:
            raise EngineUnavailable("אין הקלטת ייחוס לקול הזה")
        samples = [preview]

    ref = await asyncio.to_thread(audio.reference_clip, samples)
    try:
        url = await asyncio.to_thread(s3.upload_object, f"voices/reference/{voice['id']}.mp3", ref)
        await set_voice_fields(voice["id"], reference_audio_url=url)
        voice["reference_audio_url"] = url
    except Exception:
        logger.exception("Could not store reference for %s", voice["id"])
    return ref


async def _reference_url(voice: dict, preview_fetcher: PreviewFetcher) -> str:
    if not voice.get("reference_audio_url"):
        await get_reference(voice, preview_fetcher)
    if not voice.get("reference_audio_url"):
        raise EngineUnavailable("לא הצלחתי לשמור הקלטת ייחוס")
    return await asyncio.to_thread(s3.presigned_url, voice["reference_audio_url"], 3600)


async def ensure_minimax_voice(voice: dict, preview_fetcher: PreviewFetcher) -> str:
    if not minimax.is_enabled():
        raise EngineUnavailable("MINIMAX_API_KEY לא מוגדר")
    if voice.get("minimax_voice_id"):
        return voice["minimax_voice_id"]
    samples = await load_samples(voice)
    if not samples:
        samples = [await get_reference(voice, preview_fetcher)]
    vid = await asyncio.to_thread(minimax.clone, samples, minimax.voice_id_for(voice["id"]))
    await set_voice_fields(voice["id"], minimax_voice_id=vid)
    voice["minimax_voice_id"] = vid
    return vid


async def _require_applio(voice: dict) -> None:
    if not gpu.is_enabled():
        raise EngineUnavailable("Modal לא מוגדר")
    if voice.get("is_system"):
        raise EngineUnavailable("Applio דורש קול שנוצר עם /newvoice")
    status = voice.get("applio_status")
    if status == "ready":
        return
    if status == "training":
        raise EngineUnavailable("מודל Applio עדיין באימון")
    if status == "failed":
        raise EngineUnavailable("אימון Applio נכשל - נסה/י /prepare")
    raise EngineUnavailable("אין מודל Applio - שלח/י /prepare")


# ── TTS ──────────────────────────────────────────────────────────────────────

async def _minimax_tts(text: str, voice: dict, settings: dict, preview_fetcher: PreviewFetcher) -> bytes:
    vid = await ensure_minimax_voice(voice, preview_fetcher)
    args = (plain_text(text), vid, settings.get("speed", 1.0), settings.get("language", "he"))
    try:
        return await asyncio.to_thread(minimax.synthesize, *args)
    except RuntimeError as exc:
        if not minimax.is_missing_voice_error(exc):
            raise
        # MiniMax drops clones unused for 7 days; re-clone once and retry.
        logger.info("MiniMax voice %s expired; re-cloning", vid)
        voice["minimax_voice_id"] = None
        await set_voice_fields(voice["id"], minimax_voice_id=None)
        vid = await ensure_minimax_voice(voice, preview_fetcher)
        return await asyncio.to_thread(minimax.synthesize, plain_text(text), vid, *args[2:])


async def synthesize(
    engine: str,
    text: str,
    voice: dict,
    settings: dict,
    eleven_tts: ElevenTTS,
    preview_fetcher: PreviewFetcher,
) -> bytes:
    """Text -> MP3 in `voice` using `engine` (see engines.TTS_ENGINES)."""
    if engine == "elevenlabs":
        return await asyncio.to_thread(eleven_tts)
    if engine == "minimax":
        return await _minimax_tts(text, voice, settings, preview_fetcher)
    if engine == "modelslab":
        if not modelslab.is_enabled():
            raise EngineUnavailable("MODELSLAB_API_KEY לא מוגדר")
        ref_url = await _reference_url(voice, preview_fetcher)
        return await asyncio.to_thread(
            modelslab.text_to_speech, plain_text(text), ref_url, settings.get("language", "he"),
        )
    if engine == "minimax+applio":
        await _require_applio(voice)
        base = await _minimax_tts(text, voice, settings, preview_fetcher)
        return await asyncio.to_thread(gpu.applio_convert, voice["id"], base)
    if engine == "minimax+seedvc":
        if not gpu.is_enabled():
            raise EngineUnavailable("Modal לא מוגדר")
        base = await _minimax_tts(text, voice, settings, preview_fetcher)
        ref = await get_reference(voice, preview_fetcher)
        return await asyncio.to_thread(gpu.seedvc_convert, base, ref)
    raise ValueError(f"unknown TTS engine: {engine}")


# ── STS ──────────────────────────────────────────────────────────────────────

async def convert(
    engine: str,
    recording: bytes,
    voice: dict,
    eleven_sts: ElevenSTS,
    preview_fetcher: PreviewFetcher,
) -> bytes:
    """Recording -> MP3 in `voice` using `engine` (see engines.STS_ENGINES)."""
    if engine == "elevenlabs":
        return await asyncio.to_thread(eleven_sts)
    if engine == "applio":
        await _require_applio(voice)
        return await asyncio.to_thread(gpu.applio_convert, voice["id"], recording)
    if engine == "seedvc":
        if not gpu.is_enabled():
            raise EngineUnavailable("Modal לא מוגדר")
        ref = await get_reference(voice, preview_fetcher)
        return await asyncio.to_thread(gpu.seedvc_convert, recording, ref)
    if engine == "modelslab":
        if not modelslab.is_enabled():
            raise EngineUnavailable("MODELSLAB_API_KEY לא מוגדר")
        ref_url = await _reference_url(voice, preview_fetcher)
        src = await asyncio.to_thread(audio.to_mp3, recording, modelslab.MAX_INPUT_SECONDS)
        src_key = f"runs/modelslab/{uuid.uuid4().hex}.mp3"
        src_url = await asyncio.to_thread(s3.upload_object, src_key, src)
        signed = await asyncio.to_thread(s3.presigned_url, src_url, 3600)
        return await asyncio.to_thread(modelslab.voice_to_voice, signed, ref_url)
    raise ValueError(f"unknown STS engine: {engine}")


# ── side-by-side comparison ──────────────────────────────────────────────────

async def _timed(engine: str, label: str, job: Awaitable[bytes]) -> EngineResult:
    start = time.monotonic()
    try:
        data = await asyncio.wait_for(job, ENGINE_TIMEOUT_SECONDS)
        return EngineResult(engine, label, data, time.monotonic() - start)
    except EngineUnavailable as exc:
        return EngineResult(engine, label, None, time.monotonic() - start, str(exc))
    except asyncio.TimeoutError:
        return EngineResult(engine, label, None, time.monotonic() - start, "חרג מזמן ההמתנה")
    except Exception as exc:
        logger.exception("Engine %s failed", engine)
        return EngineResult(engine, label, None, time.monotonic() - start, f"שגיאה: {str(exc)[:150]}")


async def compare_tts(text, voice, settings, eleven_tts, preview_fetcher) -> list[EngineResult]:
    jobs = [
        _timed(e, label, synthesize(e, text, voice, settings, eleven_tts, preview_fetcher))
        for e, label in TTS_ENGINES.items()
    ]
    return list(await asyncio.gather(*jobs))


async def compare_sts(recording, voice, eleven_sts, preview_fetcher) -> list[EngineResult]:
    jobs = [
        _timed(e, label, convert(e, recording, voice, eleven_sts, preview_fetcher))
        for e, label in STS_ENGINES.items()
    ]
    return list(await asyncio.gather(*jobs))


# ── Applio training status ───────────────────────────────────────────────────

async def refresh_applio_status(voice: dict) -> str:
    """Pull training status from Modal into MongoDB. Returns the new status."""
    result = await asyncio.to_thread(gpu.applio_status, voice["id"])
    status = result.get("status")
    if status in ("ready", "failed"):
        await set_voice_fields(
            voice["id"], applio_status=status, applio_error=result.get("error"),
        )
    return status or "unknown"


async def cleanup_voice(deleted: dict, voice_doc_id: str) -> None:
    """Remove engine artifacts for a deleted voice (best-effort)."""
    if deleted.get("minimax_voice_id") and minimax.is_enabled():
        try:
            await asyncio.to_thread(minimax.delete_voice, deleted["minimax_voice_id"])
        except Exception:
            logger.exception("MiniMax delete failed")
    if deleted.get("applio_status") and gpu.is_enabled():
        try:
            await asyncio.to_thread(gpu.applio_delete, voice_doc_id)
        except Exception:
            logger.exception("Applio delete failed")
    if deleted.get("reference_audio_url"):
        try:
            await asyncio.to_thread(s3.delete_samples, [deleted["reference_audio_url"]])
        except Exception:
            logger.exception("Reference delete failed")
