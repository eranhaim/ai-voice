import os
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None

DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
    return _client[os.getenv("MONGO_DB", "voice_bot")]


# ── Users ─────────────────────────────────────────────────────────────────────

async def is_authorized(telegram_id: int) -> bool:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    return user is not None


# ── Runs ──────────────────────────────────────────────────────────────────────

async def log_run(telegram_id: int, run_type: str, text: str) -> None:
    db = get_db()
    await db.runs.insert_one({
        "telegram_id": telegram_id,
        "type": run_type,
        "text": text,
        "created_at": datetime.now(timezone.utc),
    })


# ── Voices ────────────────────────────────────────────────────────────────────

async def get_user_voice_id(telegram_id: int) -> str:
    """Return the ElevenLabs voice_id the user has selected, or the default."""
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user or not user.get("active_voice_id"):
        return DEFAULT_VOICE_ID

    voice = await db.voices.find_one({"_id": user["active_voice_id"]})
    if not voice:
        return DEFAULT_VOICE_ID
    return voice["elevenlabs_voice_id"]


async def set_active_voice(telegram_id: int, voice_doc_id: str | None) -> None:
    """Set the user's active voice. Pass None to reset to default."""
    db = get_db()
    value = ObjectId(voice_doc_id) if voice_doc_id else None
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"active_voice_id": value}},
    )


async def create_voice(
    telegram_id: int,
    name: str,
    elevenlabs_voice_id: str,
    sample_urls: list[str],
) -> str:
    db = get_db()
    result = await db.voices.insert_one({
        "telegram_id": telegram_id,
        "name": name,
        "elevenlabs_voice_id": elevenlabs_voice_id,
        "sample_urls": sample_urls,
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


async def get_user_voices(telegram_id: int) -> list[dict]:
    db = get_db()
    voices = []
    async for doc in db.voices.find({"telegram_id": telegram_id}).sort("created_at", -1):
        voices.append({
            "id": str(doc["_id"]),
            "name": doc["name"],
            "elevenlabs_voice_id": doc["elevenlabs_voice_id"],
            "sample_urls": doc.get("sample_urls", []),
            "created_at": doc["created_at"],
        })
    return voices


async def get_voice_by_id(voice_doc_id: str) -> dict | None:
    db = get_db()
    doc = await db.voices.find_one({"_id": ObjectId(voice_doc_id)})
    if not doc:
        return None
    return {
        "id": str(doc["_id"]),
        "telegram_id": doc["telegram_id"],
        "name": doc["name"],
        "elevenlabs_voice_id": doc["elevenlabs_voice_id"],
        "sample_urls": doc.get("sample_urls", []),
    }


async def delete_voice(voice_doc_id: str) -> dict | None:
    """Delete a voice and return its data (for cleanup). Returns None if not found."""
    db = get_db()
    doc = await db.voices.find_one_and_delete({"_id": ObjectId(voice_doc_id)})
    if not doc:
        return None

    await db.users.update_many(
        {"active_voice_id": ObjectId(voice_doc_id)},
        {"$set": {"active_voice_id": None}},
    )

    return {
        "elevenlabs_voice_id": doc["elevenlabs_voice_id"],
        "sample_urls": doc.get("sample_urls", []),
    }
