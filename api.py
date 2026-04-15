import os
import secrets
import asyncio
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from db import get_db

load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

app = FastAPI(title="Voice Bot Admin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_tokens: set[str] = set()

# ── Temp audio store (in-memory, auto-purged) ────────────────────────────────

_audio_store: dict[str, tuple[bytes, float]] = {}
AUDIO_TTL_SECONDS = 600


async def _purge_audio_loop():
    while True:
        await asyncio.sleep(120)
        now = time.time()
        expired = [k for k, (_, ts) in _audio_store.items() if now - ts > AUDIO_TTL_SECONDS]
        for k in expired:
            _audio_store.pop(k, None)


@app.on_event("startup")
async def _start_purge():
    asyncio.create_task(_purge_audio_loop())


def _require_auth(authorization: str | None):
    if not authorization or authorization not in _tokens:
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
    _tokens.add(token)
    return LoginResponse(token=token)


# ── Temp audio (for inline mode) ──────────────────────────────────────────────

class AudioUploadResponse(BaseModel):
    audio_id: str


@app.post("/api/audio/upload", response_model=AudioUploadResponse)
async def upload_audio(request: Request):
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")
    audio_id = secrets.token_hex(16)
    _audio_store[audio_id] = (body, time.time())
    return AudioUploadResponse(audio_id=audio_id)


@app.get("/audio/{audio_id}")
async def serve_audio(audio_id: str):
    entry = _audio_store.get(audio_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audio not found or expired")
    audio_bytes, _ = entry
    return Response(content=audio_bytes, media_type="audio/mpeg")


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


# ── Runs ──────────────────────────────────────────────────────────────────────

# ── Voices ────────────────────────────────────────────────────────────────────

class VoiceOut(BaseModel):
    telegram_id: int
    name: str
    elevenlabs_voice_id: str
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
            telegram_id=doc["telegram_id"],
            name=doc["name"],
            elevenlabs_voice_id=doc.get("elevenlabs_voice_id", ""),
            created_at=doc["created_at"].isoformat() if doc.get("created_at") else "",
        ))
    return voices


# ── Runs ──────────────────────────────────────────────────────────────────────

class RunOut(BaseModel):
    telegram_id: int
    type: str
    text: str
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
            created_at=doc["created_at"].isoformat() if doc.get("created_at") else "",
        ))
    return runs
