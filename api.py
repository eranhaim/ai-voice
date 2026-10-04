import asyncio
import io
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from fastapi import FastAPI, File, Form, HTTPException, Header, UploadFile
from pydantic import BaseModel

from audio_quality import ReferenceAudioError, validate_reference_audio
from db import CONSENT_STATUS_CONFIRMED, get_db, mark_voice_consented
from s3 import _get_client as get_s3_client

logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "5060049285")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

app = FastAPI(title="Voice Bot Admin")

ADMIN_TOKEN_TTL = timedelta(hours=12)
_tokens: dict[str, datetime] = {}


def _require_auth(authorization: str | None):
    expires_at = _tokens.get(authorization or "")
    if not expires_at or expires_at <= datetime.now(timezone.utc):
        if authorization:
            _tokens.pop(authorization, None)
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str


@app.post("/api/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    if body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Wrong password")
    token = secrets.token_hex(32)
    _tokens[token] = datetime.now(timezone.utc) + ADMIN_TOKEN_TTL
    return LoginResponse(token=token)


# ── Users ─────────────────────────────────────────────────────────────────────

class UserIn(BaseModel):
    telegram_id: int
    name: str = ""


class UserOut(BaseModel):
    telegram_id: int
    name: str
    created_at: str


@app.get("/api/users", response_model=list[UserOut])
async def list_users(authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    db = get_db()
    users = []
    async for doc in db.users.find().sort("created_at", -1):
        users.append(UserOut(
            telegram_id=doc["telegram_id"],
            name=doc.get("name", ""),
            created_at=doc["created_at"].isoformat() if doc.get("created_at") else "",
        ))
    return users


@app.post("/api/users", response_model=UserOut, status_code=201)
async def add_user(body: UserIn, authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    db = get_db()

    existing = await db.users.find_one({"telegram_id": body.telegram_id})
    if existing:
        raise HTTPException(status_code=409, detail="User already exists")

    now = datetime.now(timezone.utc)
    await db.users.insert_one({
        "telegram_id": body.telegram_id,
        "name": body.name,
        "created_at": now,
    })
    return UserOut(
        telegram_id=body.telegram_id,
        name=body.name,
        created_at=now.isoformat(),
    )


@app.delete("/api/users/{telegram_id}", status_code=204)
async def delete_user(telegram_id: int, authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    db = get_db()
    result = await db.users.delete_one({"telegram_id": telegram_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")


# ── System Voices ─────────────────────────────────────────────────────────────

class SystemVoiceIn(BaseModel):
    name: str
    elevenlabs_voice_id: str
    consent_confirmed: bool
    consent_reference: str


class SystemVoiceOut(BaseModel):
    id: str
    name: str
    elevenlabs_voice_id: str
    consent_status: str


@app.get("/api/system-voices", response_model=list[SystemVoiceOut])
async def list_system_voices(authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    db = get_db()
    voices = []
    async for doc in db.system_voices.find():
        voices.append(SystemVoiceOut(
            id=str(doc["_id"]),
            name=doc["name"],
            elevenlabs_voice_id=doc["elevenlabs_voice_id"],
            consent_status=doc.get("consent_status", "unknown"),
        ))
    return voices


@app.post("/api/system-voices", response_model=SystemVoiceOut, status_code=201)
async def add_system_voice(body: SystemVoiceIn, authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    if not body.consent_confirmed or not body.consent_reference.strip():
        raise HTTPException(
            status_code=400,
            detail="Explicit consent confirmation and a provenance reference are required.",
        )
    db = get_db()

    existing = await db.system_voices.find_one({"elevenlabs_voice_id": body.elevenlabs_voice_id})
    if existing:
        raise HTTPException(status_code=409, detail="Voice ID already exists")

    result = await db.system_voices.insert_one({
        "name": body.name,
        "elevenlabs_voice_id": body.elevenlabs_voice_id,
        "consent_status": CONSENT_STATUS_CONFIRMED,
        "consent_provenance": body.consent_reference.strip(),
        "consented_at": datetime.now(timezone.utc),
    })
    return SystemVoiceOut(
        id=str(result.inserted_id),
        name=body.name,
        elevenlabs_voice_id=body.elevenlabs_voice_id,
        consent_status=CONSENT_STATUS_CONFIRMED,
    )


@app.delete("/api/system-voices/{voice_id}", status_code=204)
async def delete_system_voice(voice_id: str, authorization: str | None = Header(default=None)):
    _require_auth(authorization)
    db = get_db()
    from bson import ObjectId
    result = await db.system_voices.delete_one({"_id": ObjectId(voice_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Voice not found")


@app.post("/api/clone-voice", response_model=SystemVoiceOut, status_code=201)
async def clone_voice_from_files(
    name: str = Form(...),
    files: list[UploadFile] = File(...),
    consent_confirmed: bool = Form(...),
    consent_reference: str = Form(...),
    authorization: str | None = Header(default=None),
):
    """Clone an explicitly authorized voice without retaining reference files."""
    _require_auth(authorization)

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if not consent_confirmed or not consent_reference.strip():
        raise HTTPException(
            status_code=400,
            detail="Explicit consent confirmation and a provenance reference are required.",
        )

    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ELEVENLABS_API_KEY not configured")

    audio_buffers = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        try:
            await asyncio.to_thread(validate_reference_audio, data)
        except ReferenceAudioError as error:
            raise HTTPException(
                status_code=400,
                detail=f"Reference {f.filename or len(audio_buffers) + 1} is unsuitable: {error}",
            ) from error
        buf = io.BytesIO(data)
        buf.name = f.filename or f"sample_{len(audio_buffers)}.mp3"
        audio_buffers.append(buf)

    if not audio_buffers:
        raise HTTPException(status_code=400, detail="All uploaded files were empty")

    logger.info("Cloning authorized voice '%s' from %d validated files", name, len(audio_buffers))

    try:
        client = ElevenLabs(api_key=api_key)
        voice = client.voices.ivc.create(
            name=name,
            files=audio_buffers,
            remove_background_noise=True,
        )
        elevenlabs_voice_id = voice.voice_id
        logger.info("IVC clone created: %s", name)
    except Exception:
        logger.exception("IVC clone failed")
        raise HTTPException(status_code=502, detail="Voice cloning provider failed. Try again later.")

    db = get_db()
    existing = await db.system_voices.find_one({"elevenlabs_voice_id": elevenlabs_voice_id})
    if existing:
        return SystemVoiceOut(
            id=str(existing["_id"]),
            name=existing["name"],
            elevenlabs_voice_id=elevenlabs_voice_id,
            consent_status=existing.get("consent_status", "unknown"),
        )

    result = await db.system_voices.insert_one({
        "name": name,
        "elevenlabs_voice_id": elevenlabs_voice_id,
        "consent_status": CONSENT_STATUS_CONFIRMED,
        "consent_provenance": consent_reference.strip(),
        "consented_at": datetime.now(timezone.utc),
    })
    return SystemVoiceOut(
        id=str(result.inserted_id),
        name=name,
        elevenlabs_voice_id=elevenlabs_voice_id,
        consent_status=CONSENT_STATUS_CONFIRMED,
    )


# ── Custom Voices ────────────────────────────────────────────────────────────

class VoiceOut(BaseModel):
    id: str
    telegram_id: int
    name: str
    elevenlabs_voice_id: str
    kind: str
    training_status: str
    consent_status: str
    sample_count: int
    created_at: str


@app.get("/api/voices", response_model=list[VoiceOut])
async def list_voices(
    telegram_id: int | None = None,
    authorization: str | None = Header(default=None),
):
    _require_auth(authorization)
    db = get_db()

    query = {}
    if telegram_id is not None:
        query["telegram_id"] = telegram_id

    voices = []
    async for doc in db.voices.find(query).sort("created_at", -1):
        voices.append(VoiceOut(
            id=str(doc["_id"]),
            telegram_id=doc["telegram_id"],
            name=doc["name"],
            elevenlabs_voice_id=doc.get("elevenlabs_voice_id", ""),
            kind=doc.get("kind", "ivc"),
            training_status=doc.get("training_status", "ready"),
            consent_status=doc.get("consent_status", "unknown"),
            sample_count=len(doc.get("sample_urls", [])),
            created_at=doc["created_at"].isoformat() if doc.get("created_at") else "",
        ))
    return voices


class ConsentUpdate(BaseModel):
    consent_reference: str


@app.patch("/api/voices/{voice_doc_id}/consent")
async def confirm_voice_consent(
    voice_doc_id: str,
    body: ConsentUpdate,
    authorization: str | None = Header(default=None),
):
    _require_auth(authorization)
    try:
        updated = await mark_voice_consented(voice_doc_id, body.consent_reference)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not updated:
        raise HTTPException(status_code=404, detail="Voice not found")
    return {"status": "confirmed"}


# ── Runs ──────────────────────────────────────────────────────────────────────

class RunOut(BaseModel):
    telegram_id: int
    type: str
    text: str
    voice_name: str
    model: str = ""
    has_audio: bool
    run_id: str
    created_at: str


@app.get("/api/runs", response_model=list[RunOut])
async def list_runs(
    telegram_id: int | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    _require_auth(authorization)
    db = get_db()

    query = {}
    if telegram_id is not None:
        query["telegram_id"] = telegram_id

    runs = []
    async for doc in db.runs.find(query).sort("created_at", -1).limit(limit):
        runs.append(RunOut(
            telegram_id=doc["telegram_id"],
            type=doc["type"],
            text=doc.get("text", ""),
            voice_name=doc.get("voice_name", ""),
            model=doc.get("model", ""),
            has_audio=bool(doc.get("input_audio_url")),
            run_id=str(doc["_id"]),
            created_at=doc["created_at"].isoformat() if doc.get("created_at") else "",
        ))
    return runs


@app.post("/api/runs/{run_id}/resend", status_code=200)
async def resend_audio(run_id: str, authorization: str | None = Header(default=None)):
    import logging
    logger = logging.getLogger(__name__)

    _require_auth(authorization)
    db = get_db()

    doc = await db.runs.find_one({"_id": ObjectId(run_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Run not found")

    audio_url = doc.get("input_audio_url")
    if not audio_url:
        raise HTTPException(status_code=404, detail="No input audio saved for this run")

    try:
        parts = audio_url.replace("s3://", "").split("/", 1)
        bucket, key = parts[0], parts[1]

        s3 = get_s3_client()
        obj = s3.get_object(Bucket=bucket, Key=key)
        audio_bytes = obj["Body"].read()
        logger.info("Fetched %d bytes from S3", len(audio_bytes))

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice",
                data={"chat_id": ADMIN_TELEGRAM_ID},
                files={"voice": ("input.ogg", audio_bytes, "audio/ogg")},
            )
            logger.info("Telegram response: %d %s", resp.status_code, resp.text[:200])
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Telegram error: {resp.text[:200]}")

        return {"status": "sent"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Resend failed")
        raise HTTPException(status_code=500, detail=str(e))
