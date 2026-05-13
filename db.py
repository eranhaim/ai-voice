import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None

SYSTEM_VOICES = [
    {"name": "הקול המפתה של נופר", "elevenlabs_voice_id": "jqcCZkN6Knx8BJ5TBdYR"},
    {"name": "הקול השכונתי של ליטל", "elevenlabs_voice_id": "Wim44P0dU9HtjyzNnFsv"},
    {"name": "הקול הצעיר של ליה", "elevenlabs_voice_id": "RSyLgiJaZVhD3kdzAKTD"},
    {"name": "הקול הלחשני של רומי", "elevenlabs_voice_id": "K8lgMMdmFr7QoEooafEf"},
    {"name": "הקול המעצבן של מאיה", "elevenlabs_voice_id": "Sm1seazb4gs7RSlUVw7c"},
    {"name": "הקול המאופק של אגם", "elevenlabs_voice_id": "flHkNRp1BlvT73UL6gyz"},
]

DEFAULT_VOICE_ID = SYSTEM_VOICES[0]["elevenlabs_voice_id"]


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
    return _client[os.getenv("MONGO_DB", "voice_bot")]


# ── System voices ─────────────────────────────────────────────────────────────

async def seed_system_voices() -> None:
    db = get_db()
    for sv in SYSTEM_VOICES:
        await db.system_voices.update_one(
            {"elevenlabs_voice_id": sv["elevenlabs_voice_id"]},
            {"$set": sv},
            upsert=True,
        )


# ── Users ─────────────────────────────────────────────────────────────────────

async def is_authorized(telegram_id: int) -> bool:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    return user is not None


# ── Effects ───────────────────────────────────────────────────────────────────

async def get_user_effect(telegram_id: int) -> str | None:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        return None
    return user.get("sound_effect")


async def set_user_effect(telegram_id: int, effect: str | None) -> None:
    db = get_db()
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"sound_effect": effect}},
    )


# ── Prompts ───────────────────────────────────────────────────────────────────

async def get_user_prompt(telegram_id: int) -> str | None:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        return None
    return user.get("audio_tag")


async def set_user_prompt(telegram_id: int, audio_tag: str | None) -> None:
    db = get_db()
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"audio_tag": audio_tag}},
    )


# ── Runs ──────────────────────────────────────────────────────────────────────

async def log_run(telegram_id: int, run_type: str, text: str) -> None:
    db = get_db()
    await db.runs.insert_one({
        "telegram_id": telegram_id,
        "type": run_type,
        "text": text,
        "created_at": datetime.now(timezone.utc),
    })
