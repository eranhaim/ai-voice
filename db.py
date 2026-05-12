import os
from datetime import datetime, timezone

from bson import ObjectId
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


async def get_system_voices() -> list[dict]:
    db = get_db()
    voices = []
    async for doc in db.system_voices.find():
        voices.append({
            "id": str(doc["_id"]),
            "name": doc["name"],
            "elevenlabs_voice_id": doc["elevenlabs_voice_id"],
        })
    return voices


# ── Users ─────────────────────────────────────────────────────────────────────

async def is_authorized(telegram_id: int) -> bool:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    return user is not None


# ── Mode (casual / premium) ───────────────────────────────────────────────────

MODE_CASUAL = "casual"
MODE_PREMIUM = "premium"


async def get_user_mode(telegram_id: int) -> str:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        return MODE_CASUAL
    return user.get("mode", MODE_CASUAL)


async def set_user_mode(telegram_id: int, mode: str) -> None:
    if mode not in (MODE_CASUAL, MODE_PREMIUM):
        raise ValueError("invalid mode")
    db = get_db()
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"mode": mode}},
    )


def voice_kind_for_mode(mode: str) -> str:
    return "rvc" if mode == MODE_PREMIUM else "elevenlabs"


# ── User settings (speed, language) ───────────────────────────────────────────

async def get_user_settings(telegram_id: int) -> dict:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        return {"speed": 1.0, "language": "he"}
    return {
        "speed": user.get("speed", 1.0),
        "language": user.get("language", "he"),
    }


async def set_user_settings(telegram_id: int, speed: float | None = None, language: str | None = None) -> None:
    db = get_db()
    update = {}
    if speed is not None:
        update["speed"] = speed
    if language is not None:
        update["language"] = language
    if update:
        await db.users.update_one(
            {"telegram_id": telegram_id},
            {"$set": update},
        )


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


async def get_voice_name(telegram_id: int) -> str:
    """Get the name of the user's active voice."""
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user or not user.get("active_voice_id"):
        return "Default"
    vid = user["active_voice_id"]
    voice = await db.system_voices.find_one({"_id": vid})
    if not voice:
        voice = await db.voices.find_one({"_id": vid})
    return voice["name"] if voice else "Default"


async def get_active_voice_doc(telegram_id: int) -> dict | None:
    """Return the full active voice doc (system or custom) for the user, with `kind` filled."""
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user or not user.get("active_voice_id"):
        return None
    vid = user["active_voice_id"]
    sv = await db.system_voices.find_one({"_id": vid})
    if sv:
        return {
            "id": str(sv["_id"]),
            "name": sv["name"],
            "elevenlabs_voice_id": sv["elevenlabs_voice_id"],
            "kind": "elevenlabs",
            "training_status": "ready",
        }
    cv = await db.voices.find_one({"_id": vid})
    if not cv:
        return None
    return {
        "id": str(cv["_id"]),
        "name": cv["name"],
        "elevenlabs_voice_id": cv.get("elevenlabs_voice_id", ""),
        "kind": cv.get("kind", "elevenlabs"),
        "training_status": cv.get("training_status", "ready"),
        "rvc_model_url": cv.get("rvc_model_url"),
        "rvc_index_url": cv.get("rvc_index_url"),
    }


# ── Runs ──────────────────────────────────────────────────────────────────────

async def log_run(telegram_id: int, run_type: str, text: str, voice_name: str = "", input_audio_url: str = "") -> None:
    db = get_db()
    doc = {
        "telegram_id": telegram_id,
        "type": run_type,
        "text": text,
        "voice_name": voice_name,
        "created_at": datetime.now(timezone.utc),
    }
    if input_audio_url:
        doc["input_audio_url"] = input_audio_url
    await db.runs.insert_one(doc)


# ── Voices ────────────────────────────────────────────────────────────────────

async def get_user_voice_id(telegram_id: int) -> str:
    """Return the ElevenLabs voice_id the user has selected, or the default."""
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user or not user.get("active_voice_id"):
        return DEFAULT_VOICE_ID

    vid = user["active_voice_id"]
    voice = await db.system_voices.find_one({"_id": vid})
    if not voice:
        voice = await db.voices.find_one({"_id": vid})
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
    kind: str = "elevenlabs",
    training_status: str = "ready",
) -> str:
    db = get_db()
    result = await db.voices.insert_one({
        "telegram_id": telegram_id,
        "name": name,
        "elevenlabs_voice_id": elevenlabs_voice_id,
        "sample_urls": sample_urls,
        "kind": kind,
        "training_status": training_status,
        "training_notified": training_status == "ready",
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


async def get_user_voices(telegram_id: int, kind: str | None = None) -> list[dict]:
    db = get_db()
    query: dict = {"telegram_id": telegram_id}
    if kind is not None:
        query["$or"] = [{"kind": kind}]
        if kind == "elevenlabs":
            query["$or"].append({"kind": {"$exists": False}})
    voices = []
    async for doc in db.voices.find(query).sort("created_at", -1):
        voices.append({
            "id": str(doc["_id"]),
            "name": doc["name"],
            "elevenlabs_voice_id": doc.get("elevenlabs_voice_id", ""),
            "kind": doc.get("kind", "elevenlabs"),
            "training_status": doc.get("training_status", "ready"),
            "sample_urls": doc.get("sample_urls", []),
            "rvc_model_url": doc.get("rvc_model_url"),
            "rvc_index_url": doc.get("rvc_index_url"),
            "created_at": doc["created_at"],
        })
    return voices


async def get_voice_by_id(voice_doc_id: str) -> dict | None:
    db = get_db()
    oid = ObjectId(voice_doc_id)
    doc = await db.system_voices.find_one({"_id": oid})
    if doc:
        return {
            "id": str(doc["_id"]),
            "telegram_id": None,
            "name": doc["name"],
            "elevenlabs_voice_id": doc["elevenlabs_voice_id"],
            "kind": "elevenlabs",
            "training_status": "ready",
            "sample_urls": [],
        }
    doc = await db.voices.find_one({"_id": oid})
    if not doc:
        return None
    return {
        "id": str(doc["_id"]),
        "telegram_id": doc.get("telegram_id"),
        "name": doc["name"],
        "elevenlabs_voice_id": doc.get("elevenlabs_voice_id", ""),
        "kind": doc.get("kind", "elevenlabs"),
        "training_status": doc.get("training_status", "ready"),
        "sample_urls": doc.get("sample_urls", []),
        "rvc_model_url": doc.get("rvc_model_url"),
        "rvc_index_url": doc.get("rvc_index_url"),
    }


async def set_voice_training_status(
    voice_doc_id: str,
    status: str,
    rvc_model_url: str | None = None,
    rvc_index_url: str | None = None,
    error: str | None = None,
) -> None:
    db = get_db()
    update: dict = {"training_status": status}
    if rvc_model_url:
        update["rvc_model_url"] = rvc_model_url
    if rvc_index_url:
        update["rvc_index_url"] = rvc_index_url
    if error:
        update["training_error"] = error
    await db.voices.update_one({"_id": ObjectId(voice_doc_id)}, {"$set": update})


async def find_unnotified_finished_voices() -> list[dict]:
    """Voices whose training finished (ready or failed) and the user hasn't been DM'd yet."""
    db = get_db()
    out = []
    async for doc in db.voices.find({
        "kind": "rvc",
        "training_status": {"$in": ["ready", "failed"]},
        "training_notified": {"$ne": True},
    }):
        out.append({
            "id": str(doc["_id"]),
            "telegram_id": doc["telegram_id"],
            "name": doc["name"],
            "training_status": doc["training_status"],
            "training_error": doc.get("training_error", ""),
        })
    return out


async def mark_voice_notified(voice_doc_id: str) -> None:
    db = get_db()
    await db.voices.update_one(
        {"_id": ObjectId(voice_doc_id)},
        {"$set": {"training_notified": True}},
    )


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
        "id": str(doc["_id"]),
        "elevenlabs_voice_id": doc.get("elevenlabs_voice_id", ""),
        "sample_urls": doc.get("sample_urls", []),
        "kind": doc.get("kind", "elevenlabs"),
        "rvc_model_url": doc.get("rvc_model_url"),
        "rvc_index_url": doc.get("rvc_index_url"),
    }
