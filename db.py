import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
    return _client[os.getenv("MONGO_DB", "voice_bot")]


async def is_authorized(telegram_id: int) -> bool:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    return user is not None


async def log_run(telegram_id: int, run_type: str, text: str) -> None:
    db = get_db()
    await db.runs.insert_one({
        "telegram_id": telegram_id,
        "type": run_type,
        "text": text,
        "created_at": datetime.now(timezone.utc),
    })
