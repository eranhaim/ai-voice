import os
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None

DEFAULT_VOICE_ID = "jqcCZkN6Knx8BJ5TBdYR"

# User-level mode constants. "casual" uses IVC clones; "premium" uses PVC clones.
MODE_CASUAL = "casual"
MODE_PREMIUM = "premium"

# Voice-level clone-tier constants.
VOICE_KIND_IVC = "ivc"
VOICE_KIND_PVC = "pvc"


def voice_kind_for_mode(mode: str) -> str:
    return VOICE_KIND_PVC if mode == MODE_PREMIUM else VOICE_KIND_IVC


# Per-voice settings the user can tune from the bot. Defaults match the values
# the bot used before this feature shipped, so behaviour for un-tuned voices
# is unchanged. STS historically used stability=0.5 (vs TTS 0.6); we keep that
# modality difference baked into the defaults, but any user override applies
# to both TTS and STS.
TTS_VOICE_SETTINGS_DEFAULTS: dict[str, float] = {
    "stability": 0.75,
    "similarity_boost": 0.95,
    "style": 0.3,
}
STS_VOICE_SETTINGS_DEFAULTS: dict[str, float] = {
    "stability": 0.70,
    "similarity_boost": 0.95,
    "style": 0.30,
}
VOICE_SETTING_KEYS = ("stability", "similarity_boost", "style")
VOICE_SETTING_MIN = 0.0
VOICE_SETTING_MAX = 1.0
VOICE_SETTING_STEP = 0.05


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
    return _client[os.getenv("MONGO_DB", "voice_bot")]


# ── System voices ─────────────────────────────────────────────────────────────


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


async def get_user_mode(telegram_id: int) -> str:
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        return MODE_CASUAL
    return user.get("mode") or MODE_CASUAL


async def set_user_mode(telegram_id: int, mode: str) -> None:
    if mode not in (MODE_CASUAL, MODE_PREMIUM):
        raise ValueError(f"invalid mode: {mode}")
    db = get_db()
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"mode": mode}},
    )


# ── Pitch match (STS source-to-target pitch normalization) ────────────────────

# Default ON: this is the highest-ROI improvement for cross-gender STS and
# is a no-op for same-gender voices (the delta falls under our noise threshold).
PITCH_MATCH_DEFAULT = True


async def get_user_pitch_match(telegram_id: int) -> bool:
    db = get_db()
    user = await db.users.find_one(
        {"telegram_id": telegram_id},
        {"pitch_match": 1},
    )
    if not user or "pitch_match" not in user:
        return PITCH_MATCH_DEFAULT
    return bool(user["pitch_match"])


async def set_user_pitch_match(telegram_id: int, enabled: bool) -> None:
    db = get_db()
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"pitch_match": bool(enabled)}},
    )


async def get_voice_target_pitch_hz(elevenlabs_voice_id: str) -> float | None:
    """Cached target-voice average F0, looked up across both voice collections."""
    db = get_db()
    doc = await db.system_voices.find_one(
        {"elevenlabs_voice_id": elevenlabs_voice_id},
        {"target_pitch_hz": 1},
    )
    if doc and doc.get("target_pitch_hz"):
        return float(doc["target_pitch_hz"])
    doc = await db.voices.find_one(
        {"elevenlabs_voice_id": elevenlabs_voice_id},
        {"target_pitch_hz": 1},
    )
    if doc and doc.get("target_pitch_hz"):
        return float(doc["target_pitch_hz"])
    return None


async def set_voice_target_pitch_hz(elevenlabs_voice_id: str, hz: float) -> None:
    """Cache a freshly-computed target F0 onto whichever voice doc owns this id."""
    db = get_db()
    result = await db.system_voices.update_one(
        {"elevenlabs_voice_id": elevenlabs_voice_id},
        {"$set": {"target_pitch_hz": float(hz)}},
    )
    if result.matched_count:
        return
    await db.voices.update_one(
        {"elevenlabs_voice_id": elevenlabs_voice_id},
        {"$set": {"target_pitch_hz": float(hz)}},
    )


# ── Per-voice settings (per user, per voice) ─────────────────────────────────

async def get_voice_settings_overrides(telegram_id: int, voice_doc_id: str) -> dict[str, float]:
    """Return any per-voice setting overrides the user has set, or {}."""
    db = get_db()
    user = await db.users.find_one(
        {"telegram_id": telegram_id},
        {"voice_settings": 1},
    )
    if not user:
        return {}
    overrides = (user.get("voice_settings") or {}).get(voice_doc_id) or {}
    return {k: float(v) for k, v in overrides.items() if k in VOICE_SETTING_KEYS}


async def get_voice_settings(
    telegram_id: int,
    voice_doc_id: str,
    modality: str = "tts",
) -> dict[str, float]:
    """Defaults for the modality, with the user's overrides layered on top."""
    base = STS_VOICE_SETTINGS_DEFAULTS if modality == "sts" else TTS_VOICE_SETTINGS_DEFAULTS
    overrides = await get_voice_settings_overrides(telegram_id, voice_doc_id)
    return {**base, **overrides}


async def set_voice_setting(
    telegram_id: int,
    voice_doc_id: str,
    key: str,
    value: float,
) -> None:
    if key not in VOICE_SETTING_KEYS:
        raise ValueError(f"invalid voice setting key: {key}")
    clamped = max(VOICE_SETTING_MIN, min(VOICE_SETTING_MAX, float(value)))
    clamped = round(clamped, 4)
    db = get_db()
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": {f"voice_settings.{voice_doc_id}.{key}": clamped}},
    )


async def reset_voice_settings(telegram_id: int, voice_doc_id: str) -> None:
    db = get_db()
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$unset": {f"voice_settings.{voice_doc_id}": ""}},
    )


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


# ── Runs ──────────────────────────────────────────────────────────────────────

async def log_run(
    telegram_id: int,
    run_type: str,
    text: str,
    voice_name: str = "",
    input_audio_url: str = "",
    model: str = "",
) -> None:
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
    if model:
        doc["model"] = model
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
    kind: str = VOICE_KIND_IVC,
    training_status: str = "ready",
) -> str:
    db = get_db()
    doc = {
        "telegram_id": telegram_id,
        "name": name,
        "elevenlabs_voice_id": elevenlabs_voice_id,
        "sample_urls": sample_urls,
        "kind": kind,
        "training_status": training_status,
        "training_notified": training_status == "ready",
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.voices.insert_one(doc)
    return str(result.inserted_id)


def _voice_to_dict(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "telegram_id": doc.get("telegram_id"),
        "name": doc["name"],
        "elevenlabs_voice_id": doc.get("elevenlabs_voice_id", ""),
        "sample_urls": doc.get("sample_urls", []),
        "kind": doc.get("kind", VOICE_KIND_IVC),
        "training_status": doc.get("training_status", "ready"),
        "training_notified": doc.get("training_notified", True),
        "created_at": doc.get("created_at"),
    }


async def get_user_voices(telegram_id: int, kind: str | None = None) -> list[dict]:
    db = get_db()
    query: dict = {"telegram_id": telegram_id}
    if kind is not None:
        # default-IVC voices may not have the `kind` field set yet, treat them as IVC.
        if kind == VOICE_KIND_IVC:
            query["$or"] = [{"kind": VOICE_KIND_IVC}, {"kind": {"$exists": False}}]
        else:
            query["kind"] = kind
    voices = []
    async for doc in db.voices.find(query).sort("created_at", -1):
        voices.append(_voice_to_dict(doc))
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
            "sample_urls": [],
            "kind": VOICE_KIND_IVC,
            "training_status": "ready",
            "training_notified": True,
        }
    doc = await db.voices.find_one({"_id": oid})
    if not doc:
        return None
    return _voice_to_dict(doc)


async def get_active_voice_doc(telegram_id: int) -> dict | None:
    """Return the user's currently-active voice doc (system or custom), or None."""
    db = get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user or not user.get("active_voice_id"):
        return None
    return await get_voice_by_id(str(user["active_voice_id"]))


async def set_voice_training_status(
    voice_doc_id: str,
    status: str,
    **extra_fields,
) -> None:
    db = get_db()
    update: dict = {"training_status": status, **extra_fields}
    # When a voice becomes ready we leave training_notified=False so the polling
    # job can DM the user. Any non-ready transition clears the notify flag too
    # so e.g. retraining a failed voice will re-notify on success.
    if status == "ready":
        update["training_notified"] = False
    await db.voices.update_one(
        {"_id": ObjectId(voice_doc_id)},
        {"$set": update},
    )


async def find_pvc_voices_in_progress() -> list[dict]:
    """Return PVC voices that still need their status polled from ElevenLabs."""
    db = get_db()
    voices = []
    async for doc in db.voices.find({
        "kind": VOICE_KIND_PVC,
        "training_status": {"$in": ["training", "verifying"]},
    }):
        voices.append(_voice_to_dict(doc))
    return voices


async def find_unnotified_finished_voices() -> list[dict]:
    """Return PVC voices whose owners should be DM'd that the voice is ready/failed."""
    db = get_db()
    voices = []
    async for doc in db.voices.find({
        "kind": VOICE_KIND_PVC,
        "training_status": {"$in": ["ready", "failed"]},
        "training_notified": {"$ne": True},
    }):
        voices.append(_voice_to_dict(doc))
    return voices


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
        "elevenlabs_voice_id": doc.get("elevenlabs_voice_id", ""),
        "sample_urls": doc.get("sample_urls", []),
        "kind": doc.get("kind", VOICE_KIND_IVC),
    }
