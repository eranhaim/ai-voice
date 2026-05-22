#!/usr/bin/env python3
"""Backfill MiniMax voice clones for all custom voices missing minimax_voice_id.

Usage (on EC2 inside the bot container):
    python backfill_minimax_voices.py
    python backfill_minimax_voices.py --dry-run
    python backfill_minimax_voices.py --force   # re-clone even if id exists
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from db import get_db, set_minimax_voice_id, VOICE_KIND_PVC  # noqa: E402
from s3 import download_sample  # noqa: E402
import minimax_tts  # noqa: E402
from bot import (  # noqa: E402
    clone_minimax_from_samples,
    text_to_speech_minimax,
    _minimax_voice_id_for_doc,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("backfill_minimax")


async def _user_language(db, telegram_id: int | None) -> str:
    if not telegram_id:
        return "he"
    user = await db.users.find_one({"telegram_id": telegram_id}, {"language": 1})
    return (user or {}).get("language") or "he"


def _needs_clone(doc: dict, force: bool) -> bool:
    if force:
        return True
    mid = doc.get("minimax_voice_id")
    return not mid


async def backfill_voice(
    doc: dict,
    *,
    dry_run: bool,
    force: bool,
    activate: bool,
) -> str:
    voice_doc_id = str(doc["_id"])
    name = doc.get("name", "?")
    status = doc.get("training_status", "ready")
    kind = doc.get("kind", "ivc")

    if kind == VOICE_KIND_PVC and status not in ("ready",):
        logger.info("SKIP %s (%s): PVC status=%s", name, voice_doc_id, status)
        return "skip_not_ready"

    if not _needs_clone(doc, force):
        logger.info("SKIP %s (%s): already has minimax_voice_id", name, voice_doc_id)
        return "skip_has_id"

    urls = doc.get("sample_urls") or []
    if not urls:
        logger.warning("SKIP %s (%s): no sample_urls", name, voice_doc_id)
        return "skip_no_samples"

    minimax_id = _minimax_voice_id_for_doc(voice_doc_id)
    db = get_db()
    lang = await _user_language(db, doc.get("telegram_id"))

    logger.info(
        "CLONE %s (%s) -> %s | %d samples | lang=%s",
        name, voice_doc_id, minimax_id, len(urls), lang,
    )
    if dry_run:
        return "dry_run"

    samples = [await asyncio.to_thread(download_sample, url) for url in urls]
    await asyncio.to_thread(clone_minimax_from_samples, samples, voice_doc_id, lang)
    await set_minimax_voice_id(voice_doc_id, minimax_id)

    if activate:
        try:
            await asyncio.to_thread(
                text_to_speech_minimax,
                "שלום.",
                minimax_id,
                1.0,
                lang,
            )
            logger.info("ACTIVATE OK %s", minimax_id)
        except Exception:
            logger.exception("ACTIVATE failed for %s (clone still saved)", minimax_id)

    return "ok"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill MiniMax clones for custom voices")
    parser.add_argument("--dry-run", action="store_true", help="List voices only, do not clone")
    parser.add_argument("--force", action="store_true", help="Re-clone even if minimax_voice_id exists")
    parser.add_argument("--limit", type=int, default=0, help="Max voices to process (0 = all)")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between clones")
    parser.add_argument("--no-activate", action="store_true", help="Skip post-clone TTS activation")
    args = parser.parse_args()

    import os
    if not os.getenv("MINIMAX_API_KEY"):
        logger.error("MINIMAX_API_KEY not set")
        return 1

    db = get_db()
    cursor = db.voices.find({}).sort("created_at", 1)

    counts: dict[str, int] = {}
    processed = 0

    async for doc in cursor:
        if args.limit and processed >= args.limit:
            break
        try:
            result = await backfill_voice(
                doc,
                dry_run=args.dry_run,
                force=args.force,
                activate=not args.no_activate,
            )
        except Exception:
            logger.exception(
                "FAILED %s (%s)",
                doc.get("name", "?"),
                doc.get("_id"),
            )
            result = "error"
        counts[result] = counts.get(result, 0) + 1
        if result in ("ok", "dry_run"):
            processed += 1
            if not args.dry_run and args.delay > 0:
                await asyncio.sleep(args.delay)

    logger.info("Done. Summary: %s", counts)
    failed = counts.get("skip_no_samples", 0)
    ok = counts.get("ok", 0)
    if ok == 0 and failed > 0 and not args.dry_run:
        logger.warning("No voices cloned — check sample_urls and PVC readiness")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
