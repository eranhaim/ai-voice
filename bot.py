import asyncio
import json
import logging
import os
import subprocess
import uuid
from io import BytesIO

import httpx
from openai import OpenAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

from db import (
    is_authorized,
    log_run,
    get_user_voice_id,
    get_voice_name,
    get_user_prompt,
    set_user_prompt,
    get_user_effect,
    set_user_effect,
    get_user_settings,
    set_user_settings,
    set_active_voice,
    create_voice,
    get_user_voices,
    get_voice_by_id,
    delete_voice,
    get_system_voices,
    get_voice_settings,
    get_voice_settings_overrides,
    set_voice_setting,
    reset_voice_settings,
    get_user_pitch_match,
    set_user_pitch_match,
    get_voice_target_pitch_hz,
    set_voice_target_pitch_hz,
    TTS_VOICE_SETTINGS_DEFAULTS,
    STS_VOICE_SETTINGS_DEFAULTS,
    VOICE_SETTING_KEYS,
    VOICE_SETTING_MIN,
    VOICE_SETTING_MAX,
    VOICE_SETTING_STEP,
    get_user_playai_voice_id,
    playai_voice_id_from_doc,
    PLAYAI_DEFAULT_VOICE_ID,
)
from s3 import upload_sample, upload_run_audio, delete_samples
import playai_tts
import pitch

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# HTTP client request logs include the full Telegram API URL, which contains the
# bot token. Keep operational logs while suppressing those sensitive URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
PLAYAI_API_KEY = os.getenv("PLAYAI_API_KEY", "")
PLAYAI_USER_ID = os.getenv("PLAYAI_USER_ID", "")

TTS_MODEL = "eleven_v3"
STS_MODEL = "eleven_multilingual_sts_v2"
PLAYAI_TTS_MODEL = os.getenv("PLAYAI_TTS_MODEL", "PlayDialog")

DEFAULT_AUDIO_TAG = "[talk like a bitch, fast, casual, informal, and always in male-addressing format in Hebrew]"
MIN_SAMPLE_DURATION = 5

NIKUD_SYSTEM_PROMPT = (
    "You add SELECTIVE Hebrew nikud (vowel diacritics) to resolve gender ambiguity.\n"
    "The speaker is a woman addressing a MAN. Add nikud ONLY to words that are "
    "gender-ambiguous so TTS pronounces them correctly as male-addressed forms.\n"
    "\n"
    "Key male suffixes:\n"
    "- לך → לְךָ (lekha, to you male)\n"
    "- איתך → אִיתְּךָ (itkha, with you male)\n"
    "- שלך → שֶׁלְּךָ (shelkha, yours male)\n"
    "- אותך → אוֹתְךָ (otkha, you male object)\n"
    "- עליך → עָלֶיךָ (alekha, on you male)\n"
    "- אתה → אַתָּה (ata, you male)\n"
    "\n"
    "Female-speaker verb forms addressing a male (add nikud to avoid female-listener sound):\n"
    "- רוצה → רוֹצָה (rotza, female speaker - not rotzeh)\n"
    "- חושבת → חוֹשֶׁבֶת (khoshevet)\n"
    "- אוהבת → אוֹהֶבֶת (ohevet)\n"
    "- מחכה → מְחַכָּה (mekhaka, female speaker waiting)\n"
    "- שומעת → שׁוֹמַעַת (shoma'at)\n"
    "- שלומך → שְׁלוֹמְךָ (shlomkha, how are you male)\n"
    "\n"
    "Homographs (when clearly addressing a male listener):\n"
    "- אלי (to you) → אֵלֶיךָ — expand to אלייך form with nikud\n"
    "- אליי (to you) → אֵלֶיךָ — same, not אֵלַי (to me)\n"
    "\n"
    "Rules:\n"
    "1. ONLY add nikud to gender-ambiguous words. Do NOT nikud every word.\n"
    "2. Wrong nikud is worse than no nikud -- TTS reads mistakes literally.\n"
    "3. Do NOT change any words, only add nikud diacritics to existing letters.\n"
    "4. Leave unambiguous words as-is -- TTS handles them from context.\n"
    "5. Reply with ONLY the text with selective nikud. No explanations."
)

ENHANCE_SYSTEM_PROMPT = (
    "You enhance Hebrew text for a flirty female voice message (text-to-speech).\n"
    "\n"
    "Your job: add ElevenLabs v3 audio tags and natural punctuation so it sounds "
    "like a real girl recorded this on her phone -- not read from a script.\n"
    "\n"
    "Audio tags to use (place in square brackets before or after relevant text):\n"
    "- Reactions: [giggles], [soft laugh], [sighs], [breathy sigh]\n"
    "- Delivery: [whispers], [playful], [teasing], [intimate], [excited]\n"
    "- Sounds: [kisses], [mmm]\n"
    "\n"
    "Punctuation for natural pacing:\n"
    "- Add ... for pauses and hesitation\n"
    "- Add ! or ? where natural\n"
    "- CAPITALIZE key words for emphasis\n"
    "- Use -- for mid-sentence breaks\n"
    "\n"
    "Rules:\n"
    "1. NEVER change, add, or remove the actual spoken words. Only insert audio tags "
    "and adjust punctuation.\n"
    "2. Less is more: 1-3 audio tags per message maximum. Not every message needs tags.\n"
    "3. Short messages (under 10 words) usually need 0-1 tags. Don't over-tag.\n"
    "4. Tags must feel natural for the context -- don't add [giggles] to a serious message.\n"
    "5. The speaker is a young Israeli woman sending a personal voice message to a man "
    "she's flirting with.\n"
    "6. Reply with ONLY the enhanced text. No explanations, no quotes around it."
)

WAITING_PROMPT = 10
WAITING_EFFECT = 11
SELECTING_DIALOGUE_VOICES, CHOOSING_DIALOGUE_MODE, WRITING_DIALOGUE, RECORDING_DIALOGUE_STS, PICKING_DIALOGUE_VOICE_FOR_TURN = 12, 13, 20, 21, 22
SELECTING_ENHANCE_VOICE, WRITING_ENHANCE_PROMPT, CONFIRMING_ENHANCE = 14, 15, 16

UNAUTHORIZED_MSG = "אין לך הרשאה להשתמש בבוט הזה."

WAITING_NAME, COLLECTING_SAMPLES = range(2)


def _get_elevenlabs() -> ElevenLabs:
    return ElevenLabs(api_key=ELEVENLABS_API_KEY)


def _get_openai() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


# ── ElevenLabs operations ────────────────────────────────────────────────────

def _ensure_brackets(tag: str) -> str:
    tag = tag.strip()
    if tag and not tag.startswith("["):
        tag = "[" + tag
    if tag and not tag.endswith("]"):
        tag = tag + "]"
    return tag


def text_to_speech(
    text: str,
    voice_id: str,
    audio_tag: str = "",
    speed: float = 1.1,
    language: str = "he",
    voice_settings: dict | None = None,
) -> bytes:
    client = _get_elevenlabs()
    audio_tag = _ensure_brackets(audio_tag) if audio_tag else ""
    tagged_text = f"{audio_tag} {text}".strip() if audio_tag else text
    settings = dict(voice_settings or TTS_VOICE_SETTINGS_DEFAULTS)
    # Keep nikud diacritics intact; normalization can rewrite Hebrew text.
    text_normalization = "off" if _has_hebrew_nikud(text) else "on"
    audio_iter = client.text_to_speech.convert(
        text=tagged_text,
        voice_id=voice_id,
        model_id=TTS_MODEL,
        output_format="mp3_44100_192",
        language_code=language,
        voice_settings=settings,
        apply_text_normalization=text_normalization,
    )
    buffer = BytesIO()
    for chunk in audio_iter:
        buffer.write(chunk)
    audio = buffer.getvalue()
    # eleven_v3 accepts voice_settings.speed but does not change output tempo;
    # apply speed via ffmpeg after generation.
    if abs(speed - 1.0) >= 1e-3:
        audio = pitch.adjust_mp3_speed(audio, speed)
    return audio


def text_to_speech_playai(
    text: str,
    voice_id: str,
    speed: float = 1.0,
    language: str = "he",
) -> bytes:
    # PlayAI handles Hebrew natively; strip nikud meant for ElevenLabs disambiguation.
    if _has_hebrew_nikud(text):
        text = _strip_nikud(text)
    if _has_hebrew(text):
        language = "he"
    return playai_tts.synthesize(text, voice_id, speed=speed, language=language)


async def resolve_playai_voice(
    active: dict | None,
    user_id: int,
) -> str | None:
    if not PLAYAI_API_KEY or not PLAYAI_USER_ID:
        return None
    if active and playai_voice_id_from_doc(active):
        return playai_voice_id_from_doc(active)
    return await get_user_playai_voice_id(user_id)


def speech_to_speech(
    audio_bytes: bytes,
    voice_id: str,
    voice_settings: dict | None = None,
) -> bytes:
    client = _get_elevenlabs()
    settings = dict(voice_settings or STS_VOICE_SETTINGS_DEFAULTS)
    settings["use_speaker_boost"] = True
    audio_iter = client.speech_to_speech.convert(
        voice_id=voice_id,
        audio=BytesIO(audio_bytes),
        model_id=STS_MODEL,
        output_format="mp3_44100_192",
        voice_settings=json.dumps(settings),
    )
    buffer = BytesIO()
    for chunk in audio_iter:
        buffer.write(chunk)
    return buffer.getvalue()


def transcribe(audio_bytes: bytes) -> str:
    client = _get_openai()
    audio_file = BytesIO(audio_bytes)
    audio_file.name = "voice.ogg"
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language="he",
    )
    return result.text


def enhance_text(text: str) -> str:
    """Run user text through GPT to inject audio tags and natural punctuation."""
    client = _get_openai()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.7,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": ENHANCE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content.strip()


def _has_hebrew_nikud(text: str) -> bool:
    return any("\u0591" <= c <= "\u05c7" for c in text)


def _strip_nikud(text: str) -> str:
    return "".join(c for c in text if not ("\u0591" <= c <= "\u05c7"))


def _has_hebrew(text: str) -> bool:
    return any("\u05d0" <= c <= "\u05ea" or c in "\u05f0\u05f1\u05f2\u05f3\u05f4" for c in text)


def _strip_nikud_response(content: str) -> str:
    result = content.strip()
    if len(result) >= 2 and result[0] in "\"'`" and result[-1] == result[0]:
        result = result[1:-1].strip()
    return result


def add_nikud(text: str) -> str:
    """Add selective Hebrew nikud for male-addressed gender disambiguation."""
    client = _get_openai()
    messages = [
        {"role": "system", "content": NIKUD_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    response = client.chat.completions.create(
        model="gpt-5.4-nano",
        temperature=0.0,
        max_completion_tokens=1000,
        messages=messages,
    )
    content = response.choices[0].message.content
    if not content:
        logger.warning("Nikud model returned empty content")
        return text

    result = _strip_nikud_response(content)
    if result == text or not _has_hebrew_nikud(result):
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": (
                "You missed gender-ambiguous words. Add nikud to EVERY ambiguous "
                "word in the text, especially: אתה, רוצה, לך, איתך, שלך, עליך, "
                "אלי/אלייך, חושבת, אוהבת, מחכה, שלומך. Reply with ONLY the full text."
            ),
        })
        retry = client.chat.completions.create(
            model="gpt-5.4-nano",
            temperature=0.0,
            max_completion_tokens=1000,
            messages=messages,
        )
        retry_content = retry.choices[0].message.content
        if retry_content:
            retry_result = _strip_nikud_response(retry_content)
            if _has_hebrew_nikud(retry_result):
                return retry_result
    return result


def clone_voice(name: str, audio_files: list[bytes]) -> str:
    client = _get_elevenlabs()
    files = []
    for i, data in enumerate(audio_files):
        buf = BytesIO(data)
        buf.name = f"sample_{i}.ogg"
        files.append(buf)
    voice = client.voices.ivc.create(
        name=name,
        files=files,
        remove_background_noise=True,
    )
    return voice.voice_id


def delete_elevenlabs_voice(voice_id: str) -> None:
    client = _get_elevenlabs()
    try:
        client.voices.delete(voice_id=voice_id)
    except Exception:
        logger.exception("Failed to delete voice %s from ElevenLabs", voice_id)


def generate_dialogue(turns: list[dict]) -> bytes:
    """Generate multi-speaker dialogue. turns = [{"voice_id": "...", "text": "..."}, ...]"""
    client = _get_elevenlabs()
    from elevenlabs.types import DialogueInput
    inputs = [DialogueInput(text=t["text"], voice_id=t["voice_id"]) for t in turns]
    audio_iter = client.text_to_dialogue.convert(
        inputs=inputs,
        model_id=TTS_MODEL,
        output_format="mp3_44100_192",
    )
    buffer = BytesIO()
    for chunk in audio_iter:
        buffer.write(chunk)
    return buffer.getvalue()


def remix_voice(voice_id: str, prompt: str, preview_text: str = "שלום מותק, מה שלומך היום? אני כל כך שמחה לדבר איתך. חיכיתי לשמוע ממך כל היום. ספר לי מה עשית, אני רוצה לשמוע הכל. באמת, אני פה בשבילך תמיד.") -> list[dict]:
    """Remix a voice and return list of previews [{generated_voice_id, audio_base64}]."""
    client = _get_elevenlabs()
    result = client.text_to_voice.remix(
        voice_id=voice_id,
        voice_description=prompt,
        text=preview_text,
    )
    previews = []
    for voice in result.previews:
        previews.append({
            "generated_voice_id": voice.generated_voice_id,
            "audio_base64": voice.audio_base_64,
        })
    return previews


def generate_sound_effect(description: str, duration: float = 10.0) -> bytes:
    client = _get_elevenlabs()
    audio_iter = client.text_to_sound_effects.convert(
        text=description,
        duration_seconds=duration,
    )
    buffer = BytesIO()
    for chunk in audio_iter:
        buffer.write(chunk)
    return buffer.getvalue()


def mix_voice_with_effect(voice_bytes: bytes, effect_bytes: bytes) -> bytes:
    """Mix voice audio with background sound effect using ffmpeg. Effect plays at lower volume."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-i", "pipe:0",
            "-i", "/tmp/_effect.mp3",
            "-filter_complex",
            "[1:a]volume=0.15,apad[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2",
            "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1",
        ],
        input=voice_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        logger.error("ffmpeg mix failed: %s", result.stderr.decode()[:200])
        return mp3_to_ogg_opus(voice_bytes)
    return result.stdout


def mp3_to_ogg_opus(mp3_bytes: bytes) -> bytes:
    result = subprocess.run(
        ["ffmpeg", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1"],
        input=mp3_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr.decode()[:200])
        return mp3_bytes
    return result.stdout


def stitch_audio_clips(clips: list[bytes]) -> bytes:
    """Concatenate multiple MP3 audio clips into one using ffmpeg."""
    import tempfile, os
    tmpdir = tempfile.mkdtemp()
    try:
        paths = []
        for i, clip in enumerate(clips):
            p = os.path.join(tmpdir, f"clip_{i}.mp3")
            with open(p, "wb") as f:
                f.write(clip)
            paths.append(p)

        list_file = os.path.join(tmpdir, "list.txt")
        with open(list_file, "w") as f:
            for p in paths:
                f.write(f"file '{p}'\n")

        result = subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"],
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error("ffmpeg stitch failed: %s", result.stderr.decode()[:200])
            return clips[0] if clips else b""
        return result.stdout
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def process_audio_with_effect(voice_bytes: bytes, effect_description: str | None) -> bytes:
    """Convert voice to OGG, optionally mixing in a background sound effect."""
    if not effect_description:
        return mp3_to_ogg_opus(voice_bytes)

    try:
        effect_bytes = generate_sound_effect(effect_description, duration=15.0)
        with open("/tmp/_effect.mp3", "wb") as f:
            f.write(effect_bytes)
        return mix_voice_with_effect(voice_bytes, effect_bytes)
    except Exception:
        logger.exception("Sound effect generation/mixing failed, returning voice only")
        return mp3_to_ogg_opus(voice_bytes)


# ── Pitch matching (STS source → target F0 normalization) ────────────────────

def _fetch_voice_preview_audio(elevenlabs_voice_id: str) -> bytes | None:
    """Return MP3 bytes of the ElevenLabs preview clip for a voice.

    Used to estimate the target voice's average F0 once and cache the result.
    ElevenLabs exposes a `preview_url` for fully-trained voices.
    """
    try:
        voice = _get_elevenlabs().voices.get(voice_id=elevenlabs_voice_id)
    except Exception:
        logger.exception("Failed to fetch voice metadata for %s", elevenlabs_voice_id)
        return None
    preview_url = getattr(voice, "preview_url", None)
    if not preview_url:
        logger.info("Voice %s has no preview_url; cannot detect target pitch", elevenlabs_voice_id)
        return None
    try:
        resp = httpx.get(preview_url, timeout=15.0)
        resp.raise_for_status()
        return resp.content
    except Exception:
        logger.exception("Failed to fetch preview audio for %s", elevenlabs_voice_id)
        return None


async def _resolve_target_pitch_hz(elevenlabs_voice_id: str) -> float | None:
    """Cached lookup; lazily computes and persists target F0 on first STS call."""
    cached = await get_voice_target_pitch_hz(elevenlabs_voice_id)
    if cached:
        return cached

    preview = await asyncio.to_thread(_fetch_voice_preview_audio, elevenlabs_voice_id)
    if not preview:
        return None

    target_hz = await asyncio.to_thread(pitch.estimate_f0_hz, preview)
    if not target_hz:
        return None

    try:
        await set_voice_target_pitch_hz(elevenlabs_voice_id, target_hz)
    except Exception:
        logger.exception("Failed to cache target pitch for %s", elevenlabs_voice_id)
    return target_hz


async def maybe_pitch_match(
    user_id: int,
    audio_bytes: bytes,
    target_elevenlabs_voice_id: str,
) -> bytes:
    """Apply source→target pitch shifting to `audio_bytes` if the user has it
    enabled and the source/target pitches differ enough to bother.

    Returns the (possibly shifted) audio bytes. Any failure path returns the
    original audio so STS still works.
    """
    try:
        if not await get_user_pitch_match(user_id):
            return audio_bytes

        target_hz = await _resolve_target_pitch_hz(target_elevenlabs_voice_id)
        if not target_hz:
            return audio_bytes

        source_hz = await asyncio.to_thread(pitch.estimate_f0_hz, audio_bytes)
        if not source_hz:
            return audio_bytes

        shift = pitch.compute_shift_semitones(source_hz, target_hz)
        if shift == 0.0:
            logger.info(
                "Pitch match skipped: src=%.1fHz tgt=%.1fHz (below threshold)",
                source_hz, target_hz,
            )
            return audio_bytes

        logger.info(
            "Pitch match: src=%.1fHz tgt=%.1fHz shift=%+.2f semitones",
            source_hz, target_hz, shift,
        )
        shifted = await asyncio.to_thread(pitch.pitch_shift_ogg, audio_bytes, shift)
        return shifted
    except Exception:
        logger.exception("Pitch match failed; using original audio")
        return audio_bytes


# ── Normal message handlers ──────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    text = update.message.text
    if not text or not text.strip():
        return

    if len(text) > 5000:
        await update.message.reply_text("הטקסט ארוך מדי. מקסימום 5,000 תווים.")
        return

    active = await get_active_voice_doc(user_id)
    voice_id = active["elevenlabs_voice_id"] if active else await get_user_voice_id(user_id)
    vname = active["name"] if active else await get_voice_name(user_id)
    voice_settings_override = (
        await get_voice_settings(user_id, active["id"], modality="tts") if active else None
    )
    audio_tag = await get_user_prompt(user_id) or DEFAULT_AUDIO_TAG
    effect = await get_user_effect(user_id)
    settings = await get_user_settings(user_id)
    logger.info("TTS from %d with voice %s: %d chars", user_id, voice_id, len(text))
    await update.message.reply_chat_action("record_voice")

    try:
        nikud_text = await asyncio.to_thread(add_nikud, text)
        if nikud_text and nikud_text != text:
            logger.info("Nikud: %s", nikud_text[:200])
            text = nikud_text
        elif not _has_hebrew_nikud(nikud_text or ""):
            logger.warning("Nikud unchanged for %d chars: %s", len(text), text[:120])
    except Exception:
        logger.exception("Nikud failed, using original text")

    try:
        playai_voice_id = await resolve_playai_voice(active, user_id)
        tts_model = TTS_MODEL
        if playai_voice_id:
            try:
                audio_data = await asyncio.to_thread(
                    text_to_speech_playai,
                    text, playai_voice_id, settings["speed"], settings["language"],
                )
                tts_model = PLAYAI_TTS_MODEL
                logger.info("TTS via PlayAI voice %s", playai_voice_id[:60])
            except Exception:
                logger.exception("PlayAI TTS failed, falling back to ElevenLabs")
                audio_data = text_to_speech(
                    text, voice_id, audio_tag, settings["speed"], settings["language"],
                    voice_settings=voice_settings_override,
                )
                tts_model = TTS_MODEL
        else:
            audio_data = text_to_speech(
                text, voice_id, audio_tag, settings["speed"], settings["language"],
                voice_settings=voice_settings_override,
            )
        if not audio_data:
            logger.warning("TTS returned empty audio, retrying without audio tag")
            if playai_voice_id:
                audio_data = await asyncio.to_thread(
                    text_to_speech_playai,
                    text, playai_voice_id, settings["speed"], settings["language"],
                )
                if audio_data:
                    tts_model = PLAYAI_TTS_MODEL
            else:
                audio_data = text_to_speech(
                    text, voice_id, speed=settings["speed"], language=settings["language"],
                    voice_settings=voice_settings_override,
                )
        if not audio_data:
            await update.message.reply_text("לא הצלחתי ליצור הקלטה. נסה/י שוב.")
            return
        ogg_data = process_audio_with_effect(audio_data, effect)
        logger.info("TTS done: %d bytes -> %d bytes ogg", len(audio_data), len(ogg_data))
        await update.message.reply_voice(voice=ogg_data)
        await log_run(user_id, "tts", text, vname, model=tts_model)
    except Exception:
        logger.exception("TTS failed")
        await update.message.reply_text("משהו השתבש. נסה/י שוב.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    active = await get_active_voice_doc(user_id)
    voice_id = active["elevenlabs_voice_id"] if active else await get_user_voice_id(user_id)
    vname = active["name"] if active else await get_voice_name(user_id)
    voice_settings_override = (
        await get_voice_settings(user_id, active["id"], modality="sts") if active else None
    )
    effect = await get_user_effect(user_id)
    logger.info("STS from %d with voice %s: duration=%ss", user_id, voice_id, voice.duration)
    await update.message.reply_chat_action("record_voice")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_data = await file.download_as_bytearray()
        audio_bytes = bytes(audio_data)

        input_audio_url = ""
        try:
            filename = f"input_{uuid.uuid4().hex}.ogg"
            input_audio_url = upload_run_audio(user_id, filename, audio_bytes)
        except Exception:
            logger.exception("Failed to save input audio to S3")

        transcription = ""
        try:
            transcription = transcribe(audio_bytes)
            logger.info("Transcription: %s", transcription[:100])
        except Exception:
            logger.exception("Transcription failed, continuing with voice conversion")

        sts_input = await maybe_pitch_match(user_id, audio_bytes, voice_id)
        converted = speech_to_speech(sts_input, voice_id, voice_settings=voice_settings_override)
        ogg_data = process_audio_with_effect(converted, effect)
        logger.info("STS done: %d bytes -> %d bytes ogg", len(converted), len(ogg_data))

        await update.message.reply_voice(voice=ogg_data)
        await log_run(user_id, "sts", transcription, vname, input_audio_url, model=STS_MODEL)
    except Exception:
        logger.exception("STS failed")
        await update.message.reply_text("משהו השתבש. נסה/י שוב.")


# ── /voices — select active voice ────────────────────────────────────────────

async def cmd_voices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    current_voice_id = await get_user_voice_id(user_id)
    buttons: list[list[InlineKeyboardButton]] = []
    system_voices = await get_system_voices()
    custom_voices = await get_user_voices(user_id)
    for sv in system_voices:
        is_active = sv["elevenlabs_voice_id"] == current_voice_id
        label = (">> " if is_active else "") + sv["name"]
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"voice_select:{sv['id']}"),
            InlineKeyboardButton("🎛", callback_data=f"voice_tune:{sv['id']}"),
        ])
    for v in custom_voices:
        is_active = v["elevenlabs_voice_id"] == current_voice_id
        label = (">> " if is_active else "") + v["name"]
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"voice_select:{v['id']}"),
            InlineKeyboardButton("🎛", callback_data=f"voice_tune:{v['id']}"),
        ])
    header = "בחר/י קול פעיל:\n(>> מסמן את הנוכחי, 🎛 לכוונון)"

    await update.message.reply_text(
        header,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_voice_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data.replace("voice_select:", "")

    voice = await get_voice_by_id(data)
    if not voice:
        await query.edit_message_text("הקול לא נמצא.")
        return
    await set_active_voice(user_id, data)
    await query.edit_message_text(f"עברת לקול: {voice['name']}")


# ── Per-voice tuning (stability / similarity / style) ────────────────────────

VOICE_TUNE_LABELS_HE = {
    "stability": "יציבות",
    "similarity_boost": "דמיון לקול",
    "style": "סגנון",
}


def _build_tune_message_and_keyboard(
    voice_name: str,
    voice_doc_id: str,
    values: dict[str, float],
    overrides: dict[str, float],
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"כיוונון של \"{voice_name}\":"]
    for key in VOICE_SETTING_KEYS:
        label_he = VOICE_TUNE_LABELS_HE[key]
        suffix = "" if key in overrides else "  (ברירת מחדל)"
        lines.append(f"• {label_he}: {values[key]:.2f}{suffix}")
    lines.append("")
    lines.append("כל לחיצה מזיזה ב-0.05.")

    rows: list[list[InlineKeyboardButton]] = []
    for key in VOICE_SETTING_KEYS:
        label_he = VOICE_TUNE_LABELS_HE[key]
        rows.append([
            InlineKeyboardButton(f"{label_he} −", callback_data=f"voice_tune_set:{voice_doc_id}:{key}:dec"),
            InlineKeyboardButton(f"{values[key]:.2f}", callback_data="voice_tune_noop"),
            InlineKeyboardButton(f"{label_he} +", callback_data=f"voice_tune_set:{voice_doc_id}:{key}:inc"),
        ])
    rows.append([
        InlineKeyboardButton("איפוס לברירת מחדל", callback_data=f"voice_tune_reset:{voice_doc_id}"),
        InlineKeyboardButton("סגירה", callback_data="voice_tune_close"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _render_voice_tune(query, user_id: int, voice_doc_id: str) -> None:
    voice = await get_voice_by_id(voice_doc_id)
    if not voice:
        await query.edit_message_text("הקול לא נמצא.")
        return
    overrides = await get_voice_settings_overrides(user_id, voice_doc_id)
    # Display value uses TTS defaults as the "base" view (stability=0.6); STS just has
    # a slightly different stability default but the same per-voice override applies.
    values = {**TTS_VOICE_SETTINGS_DEFAULTS, **overrides}
    text, kb = _build_tune_message_and_keyboard(voice["name"], voice_doc_id, values, overrides)
    await query.edit_message_text(text, reply_markup=kb)


async def handle_voice_tune(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: tapping the 🎛 button on a voice row."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    voice_doc_id = query.data.replace("voice_tune:", "")
    await _render_voice_tune(query, user_id, voice_doc_id)


async def handle_voice_tune_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Increment or decrement one of the per-voice settings."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        _, voice_doc_id, key, direction = query.data.split(":", 3)
    except ValueError:
        return
    if key not in VOICE_SETTING_KEYS:
        return

    overrides = await get_voice_settings_overrides(user_id, voice_doc_id)
    current = overrides.get(key, TTS_VOICE_SETTINGS_DEFAULTS[key])
    delta = VOICE_SETTING_STEP if direction == "inc" else -VOICE_SETTING_STEP
    new_value = round(max(VOICE_SETTING_MIN, min(VOICE_SETTING_MAX, current + delta)), 4)
    await set_voice_setting(user_id, voice_doc_id, key, new_value)
    await _render_voice_tune(query, user_id, voice_doc_id)


async def handle_voice_tune_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("שוחזר לברירת מחדל")
    user_id = query.from_user.id
    voice_doc_id = query.data.replace("voice_tune_reset:", "")
    await reset_voice_settings(user_id, voice_doc_id)
    await _render_voice_tune(query, user_id, voice_doc_id)


async def handle_voice_tune_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("כוונון נשמר.")


async def handle_voice_tune_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


# ── /deletevoice — remove a custom voice ─────────────────────────────────────

async def cmd_deletevoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    voices = await get_user_voices(user_id)
    if not voices:
        await update.message.reply_text("אין לך קולות מותאמים.")
        return

    buttons = []
    for v in voices:
        buttons.append([InlineKeyboardButton(f"מחק: {v['name']}", callback_data=f"voice_delete:{v['id']}")])

    await update.message.reply_text(
        "איזה קול למחוק?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_voice_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    voice_doc_id = query.data.replace("voice_delete:", "")
    deleted = await delete_voice(voice_doc_id)

    if not deleted:
        await query.edit_message_text("הקול לא נמצא.")
        return

    delete_elevenlabs_voice(deleted["elevenlabs_voice_id"])
    try:
        delete_samples(deleted["sample_urls"])
    except Exception:
        logger.exception("Failed to delete S3 samples")

    await query.edit_message_text("הקול נמחק.")


# ── /newvoice — create a cloned voice ────────────────────────────────────────

async def newvoice_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return ConversationHandler.END

    await update.message.reply_text("איזה שם לתת לקול החדש?")
    return WAITING_NAME


async def newvoice_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("שלח/י שם.")
        return WAITING_NAME

    context.user_data["new_voice_name"] = name
    context.user_data["new_voice_samples"] = []
    await update.message.reply_text(
        f"שם הקול: {name}\n\n"
        "עכשיו שלח/י הקלטות קוליות של האדם הזה.\n"
        "אפשר לשלוח כמה קבצים ביחד בבת אחת!\n"
        f"כל הקלטה חייבת להיות לפחות {MIN_SAMPLE_DURATION} שניות.\n"
        "שלח/י /done בסיום, או /cancel לביטול."
    )
    return COLLECTING_SAMPLES


_AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".flac",
    ".caf", ".aiff", ".aif", ".wma", ".mp4", ".mov", ".webm",
}


async def newvoice_sample(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    voice = update.message.voice or update.message.audio
    if not voice and update.message.document:
        doc = update.message.document
        mime = (doc.mime_type or "").lower()
        fname = (doc.file_name or "").lower()
        ext = ("." + fname.rsplit(".", 1)[-1]) if "." in fname else ""
        is_audio = (
            mime.startswith("audio/")
            or mime.startswith("video/")
            or ext in _AUDIO_EXTENSIONS
        )
        if is_audio:
            voice = doc
        else:
            logger.info(
                "newvoice_sample: rejected document mime=%s name=%s",
                doc.mime_type, doc.file_name,
            )
    if not voice:
        await update.message.reply_text("שלח/י הקלטה קולית, /done לסיום, או /cancel לביטול.")
        return COLLECTING_SAMPLES

    reported_dur = getattr(voice, "duration", None) or 0
    if reported_dur and reported_dur < MIN_SAMPLE_DURATION:
        await update.message.reply_text(
            f"ההקלטה קצרה מדי ({reported_dur} שניות). "
            f"כל הקלטה חייבת להיות לפחות {MIN_SAMPLE_DURATION} שניות."
        )
        return COLLECTING_SAMPLES

    samples = context.user_data.get("new_voice_samples", [])
    if len(samples) >= 25:
        await update.message.reply_text("הגעת למקסימום 25 דגימות. שלח/י /done ליצירת הקול.")
        return COLLECTING_SAMPLES

    file = await context.bot.get_file(voice.file_id)
    data = await file.download_as_bytearray()
    audio_bytes = bytes(data)
    samples.append(audio_bytes)
    context.user_data["new_voice_samples"] = samples

    mgid = update.message.media_group_id
    if mgid:
        prev_mgid = context.user_data.get("_last_media_group_id")
        context.user_data["_last_media_group_id"] = mgid
        if mgid == prev_mgid:
            return COLLECTING_SAMPLES

    await update.message.reply_text(
        f"דגימה {len(samples)} התקבלה. "
        f"שלח/י עוד או /done ליצירת הקול ({len(samples)}/25)."
    )
    return COLLECTING_SAMPLES


async def _newvoice_done_casual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    samples = context.user_data.get("new_voice_samples", [])
    name = context.user_data.get("new_voice_name", "ללא שם")

    await update.message.reply_text(
        f"יוצר את הקול \"{name}\" מ-{len(samples)} דגימה/ות... זה עלול לקחת רגע."
    )
    await update.message.reply_chat_action("typing")

    try:
        sample_urls = []
        for i, data in enumerate(samples):
            filename = f"{uuid.uuid4().hex}.ogg"
            url = upload_sample(user_id, filename, data)
            sample_urls.append(url)

        elevenlabs_voice_id = clone_voice(name, samples)
        logger.info("Cloned voice %s -> %s", name, elevenlabs_voice_id)

        voice_doc_id = await create_voice(user_id, name, elevenlabs_voice_id, sample_urls)
        await set_active_voice(user_id, voice_doc_id)

        await update.message.reply_text(
            f"הקול \"{name}\" נוצר בהצלחה והוגדר כפעיל!\n"
            "השתמש/י ב-/voices כדי לעבור בין קולות."
        )
    except Exception:
        logger.exception("Voice creation failed")
        await update.message.reply_text("יצירת הקול נכשלה. נסה/י שוב.")
    return ConversationHandler.END


async def newvoice_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    samples = context.user_data.get("new_voice_samples", [])
    if not samples:
        await update.message.reply_text("לא התקבלו דגימות. שלח/י לפחות הקלטה אחת.")
        return COLLECTING_SAMPLES
    return await _newvoice_done_casual(update, context)


def _clear_newvoice_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in (
        "new_voice_name", "new_voice_samples", "_last_media_group_id",
    ):
        context.user_data.pop(k, None)


async def newvoice_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_newvoice_state(context)
    await update.message.reply_text("יצירת הקול בוטלה.")
    return ConversationHandler.END


# ── /prompt — edit audio tag ──────────────────────────────────────────────────

async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return ConversationHandler.END

    current = await get_user_prompt(user_id) or DEFAULT_AUDIO_TAG
    await update.message.reply_text(
        f"סגנון הדיבור הנוכחי:\n{current}\n\n"
        "שלח/י סגנון חדש, או /reset לחזרה לברירת מחדל, או /cancel לביטול.\n\n"
        "כתב/י באנגלית בתוך סוגריים מרובעים. אפשר לשלב כמה כיוונים:\n\n"
        "רגשות ואופי:\n"
        "[warm, intimate, playful]\n"
        "[excited, cheerful, energetic]\n"
        "[calm, soothing, gentle]\n\n"
        "סגנון דיבור:\n"
        "[whispering, seductive]\n"
        "[confident, assertive]\n"
        "[soft, breathy, intimate]\n\n"
        "תגובות אנושיות (מוסיפות טבעיות):\n"
        "[occasional soft laughs]\n"
        "[sighs between sentences]\n\n"
        "דוגמה מלאה:\n"
        "[warm, intimate Israeli girl, relaxed and flirty, with natural Hebrew intonation and occasional giggles]"
    )
    return WAITING_PROMPT


async def prompt_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    new_prompt = update.message.text.strip()
    if not new_prompt:
        await update.message.reply_text("שלח/י סגנון דיבור.")
        return WAITING_PROMPT

    await set_user_prompt(user_id, new_prompt)
    await update.message.reply_text(f"סגנון הדיבור עודכן:\n{new_prompt}")
    return ConversationHandler.END


async def prompt_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    await set_user_prompt(user_id, None)
    await update.message.reply_text(f"סגנון הדיבור חזר לברירת מחדל:\n{DEFAULT_AUDIO_TAG}")
    return ConversationHandler.END


async def prompt_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("עריכת סגנון הדיבור בוטלה.")
    return ConversationHandler.END


# ── /dialogue — multi-voice dialogue ──────────────────────────────────────────

async def cmd_dialogue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return ConversationHandler.END

    system_voices = await get_system_voices()
    custom_voices = await get_user_voices(user_id)
    all_voices = system_voices + custom_voices

    context.user_data["dialogue_available"] = all_voices
    context.user_data["dialogue_selected"] = []

    buttons = []
    for i, v in enumerate(all_voices):
        buttons.append([InlineKeyboardButton(v["name"], callback_data=f"dlg_pick:{i}")])
    buttons.append([InlineKeyboardButton("סיימתי לבחור >>", callback_data="dlg_pick:done")])

    await update.message.reply_text(
        "בחר/י קולות לשיחה (לחץ/י על כל קול שרוצים, ואז \"סיימתי\"):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SELECTING_DIALOGUE_VOICES


async def handle_dialogue_voice_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data.replace("dlg_pick:", "")
    available = context.user_data.get("dialogue_available", [])
    selected = context.user_data.get("dialogue_selected", [])

    if data == "done":
        if len(selected) < 2:
            await query.edit_message_text("צריך לבחור לפחות 2 קולות. נסה/י שוב עם /dialogue")
            return ConversationHandler.END

        context.user_data["dialogue_voices"] = selected
        mapping = "\n".join(f"{i+1} = {v['name']}" for i, v in enumerate(selected))
        buttons = [
            [InlineKeyboardButton("TTS — כתיבת טקסט", callback_data="dlg_mode:tts")],
            [InlineKeyboardButton("STS — הקלטות קוליות", callback_data="dlg_mode:sts")],
        ]
        await query.edit_message_text(
            f"קולות שנבחרו:\n{mapping}\n\n"
            "איך תרצה/י ליצור את השיחה?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return CHOOSING_DIALOGUE_MODE

    idx = int(data)
    if idx < len(available):
        voice = available[idx]
        if voice not in selected:
            selected.append(voice)
            context.user_data["dialogue_selected"] = selected

        names = ", ".join(v["name"] for v in selected)
        buttons = []
        for i, v in enumerate(available):
            label = (">> " if v in selected else "") + v["name"]
            buttons.append([InlineKeyboardButton(label, callback_data=f"dlg_pick:{i}")])
        buttons.append([InlineKeyboardButton(f"סיימתי לבחור ({len(selected)}) >>", callback_data="dlg_pick:done")])

        await query.edit_message_text(
            f"נבחרו: {names}\nלחץ/י על עוד קולות או \"סיימתי\":",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    return SELECTING_DIALOGUE_VOICES


async def handle_dialogue_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    mode = query.data.replace("dlg_mode:", "")
    voices = context.user_data.get("dialogue_voices", [])
    mapping = "\n".join(f"{i+1} = {v['name']}" for i, v in enumerate(voices))

    if mode == "tts":
        await query.edit_message_text(
            f"קולות:\n{mapping}\n\n"
            "כתוב/י את השיחה, שורה לכל תור:\n"
            "1: טקסט ראשון\n"
            "2: טקסט שני\n"
            "1: טקסט שלישי\n\n"
            "שלח/י /cancel לביטול."
        )
        return WRITING_DIALOGUE
    else:
        context.user_data["dialogue_sts_turns"] = []
        voice_buttons = []
        for i, v in enumerate(voices):
            voice_buttons.append([InlineKeyboardButton(f"{i+1}: {v['name']}", callback_data=f"dlg_sts_voice:{i}")])
        context.user_data["dialogue_sts_voice_buttons"] = voice_buttons

        await query.edit_message_text(
            f"קולות:\n{mapping}\n\n"
            "מצב הקלטה: שלח/י הקלטה קולית, ואז בחר/י לאיזה קול לשייך אותה.\n"
            "שלח/י /done בסיום, או /cancel לביטול."
        )
        return RECORDING_DIALOGUE_STS


async def handle_dialogue_sts_recording(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    voice = update.message.voice or update.message.audio
    if not voice:
        await update.message.reply_text("שלח/י הקלטה קולית, /done לסיום, או /cancel לביטול.")
        return RECORDING_DIALOGUE_STS

    file = await context.bot.get_file(voice.file_id)
    audio_data = await file.download_as_bytearray()
    context.user_data["dialogue_sts_pending_audio"] = bytes(audio_data)

    voice_buttons = context.user_data.get("dialogue_sts_voice_buttons", [])
    await update.message.reply_text(
        "לאיזה קול לשייך את ההקלטה?",
        reply_markup=InlineKeyboardMarkup(voice_buttons),
    )
    return PICKING_DIALOGUE_VOICE_FOR_TURN


async def handle_dialogue_sts_voice_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    idx = int(query.data.replace("dlg_sts_voice:", ""))
    voices = context.user_data.get("dialogue_voices", [])
    pending_audio = context.user_data.pop("dialogue_sts_pending_audio", None)

    if not pending_audio or idx >= len(voices):
        await query.edit_message_text("שגיאה. נסה/י שוב.")
        return RECORDING_DIALOGUE_STS

    turns = context.user_data.get("dialogue_sts_turns", [])
    turns.append({"audio": pending_audio, "voice_idx": idx})
    context.user_data["dialogue_sts_turns"] = turns

    await query.edit_message_text(
        f"תור {len(turns)} נשמר עם קול: {voices[idx]['name']}\n"
        "שלח/י הקלטה נוספת, /done לסיום, או /cancel לביטול."
    )
    return RECORDING_DIALOGUE_STS


async def handle_dialogue_sts_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    turns = context.user_data.get("dialogue_sts_turns", [])
    voices = context.user_data.get("dialogue_voices", [])

    if not turns:
        await update.message.reply_text("לא התקבלו הקלטות. שלח/י הקלטה או /cancel.")
        return RECORDING_DIALOGUE_STS

    await update.message.reply_text(f"ממיר {len(turns)} הקלטות... זה עלול לקחת רגע.")
    await update.message.reply_chat_action("record_voice")

    try:
        converted_clips = []
        for turn in turns:
            picked = voices[turn["voice_idx"]]
            voice_id = picked["elevenlabs_voice_id"]
            override = await get_voice_settings(user_id, picked["id"], modality="sts")
            sts_input = await maybe_pitch_match(user_id, turn["audio"], voice_id)
            converted = speech_to_speech(sts_input, voice_id, voice_settings=override)
            converted_clips.append(converted)

        stitched = stitch_audio_clips(converted_clips)
        effect = await get_user_effect(user_id)
        ogg_data = process_audio_with_effect(stitched, effect)
        logger.info("STS Dialogue done: %d turns, %d bytes", len(turns), len(ogg_data))

        await update.message.reply_voice(voice=ogg_data)
        voice_names = ", ".join(voices[t["voice_idx"]]["name"] for t in turns)
        await log_run(user_id, "dialogue_sts", f"{len(turns)} turns", voice_names, model=STS_MODEL)
    except Exception:
        logger.exception("STS Dialogue failed")
        await update.message.reply_text("יצירת השיחה נכשלה. נסה/י שוב.")

    context.user_data.pop("dialogue_available", None)
    context.user_data.pop("dialogue_selected", None)
    context.user_data.pop("dialogue_voices", None)
    context.user_data.pop("dialogue_sts_turns", None)
    context.user_data.pop("dialogue_sts_voice_buttons", None)
    return ConversationHandler.END


async def handle_dialogue_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    voices = context.user_data.get("dialogue_voices", [])
    text = update.message.text.strip()

    if not text:
        await update.message.reply_text("שלח/י טקסט שיחה או /cancel לביטול.")
        return WRITING_DIALOGUE

    turns = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            await update.message.reply_text(
                f"שורה לא תקינה: \"{line}\"\n"
                "הפורמט: מספר: טקסט (למשל 1: שלום)"
            )
            return WRITING_DIALOGUE
        num_str, content = line.split(":", 1)
        try:
            idx = int(num_str.strip()) - 1
        except ValueError:
            await update.message.reply_text(
                f"מספר לא תקין: \"{num_str}\"\n"
                "השתמש/י במספרים 1, 2, 3..."
            )
            return WRITING_DIALOGUE
        if idx < 0 or idx >= len(voices):
            await update.message.reply_text(
                f"קול {idx+1} לא קיים. יש {len(voices)} קולות."
            )
            return WRITING_DIALOGUE
        turns.append({"voice_id": voices[idx]["elevenlabs_voice_id"], "text": content.strip()})

    if not turns:
        await update.message.reply_text("לא זוהו שורות שיחה. נסה/י שוב.")
        return WRITING_DIALOGUE

    await update.message.reply_chat_action("record_voice")
    await update.message.reply_text(f"מייצר שיחה עם {len(turns)} תורות... זה עלול לקחת רגע.")

    try:
        audio_data = generate_dialogue(turns)
        effect = await get_user_effect(user_id)
        ogg_data = process_audio_with_effect(audio_data, effect)
        logger.info("Dialogue done: %d turns, %d bytes", len(turns), len(ogg_data))
        await update.message.reply_voice(voice=ogg_data)
        dialogue_text = " | ".join(f"{t['text']}" for t in turns)
        voice_names = ", ".join(v["name"] for v in voices)
        await log_run(user_id, "dialogue", dialogue_text, voice_names, model=TTS_MODEL)
    except Exception:
        logger.exception("Dialogue generation failed")
        await update.message.reply_text("יצירת השיחה נכשלה. נסה/י שוב.")

    context.user_data.pop("dialogue_available", None)
    context.user_data.pop("dialogue_selected", None)
    context.user_data.pop("dialogue_voices", None)
    return ConversationHandler.END


async def dialogue_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ["dialogue_available", "dialogue_selected", "dialogue_voices",
                "dialogue_sts_turns", "dialogue_sts_voice_buttons", "dialogue_sts_pending_audio"]:
        context.user_data.pop(key, None)
    await update.message.reply_text("יצירת השיחה בוטלה.")
    return ConversationHandler.END


# ── /settings — speed and language ─────────────────────────────────────────────

LANG_OPTIONS = {
    "he": "עברית",
    "en": "English",
    "ar": "العربية",
    "ru": "Русский",
    "fr": "Français",
    "es": "Español",
}


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    settings = await get_user_settings(user_id)
    pitch_match_on = await get_user_pitch_match(user_id)
    lang_name = LANG_OPTIONS.get(settings["language"], settings["language"])

    pitch_buttons = [
        InlineKeyboardButton(
            f"{'>> ' if pitch_match_on else ''}התאמת גובה: פעיל",
            callback_data="set_pitch:on",
        ),
        InlineKeyboardButton(
            f"{'>> ' if not pitch_match_on else ''}התאמת גובה: כבוי",
            callback_data="set_pitch:off",
        ),
    ]

    speed_buttons = []
    for s in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
        label = f"{'>> ' if settings['speed'] == s else ''}{s}x"
        speed_buttons.append(InlineKeyboardButton(label, callback_data=f"set_speed:{s}"))

    lang_buttons = []
    for code, name in LANG_OPTIONS.items():
        label = f"{'>> ' if settings['language'] == code else ''}{name}"
        lang_buttons.append(InlineKeyboardButton(label, callback_data=f"set_lang:{code}"))

    buttons = [
        pitch_buttons,
        speed_buttons[:3], speed_buttons[3:],
        lang_buttons[:3], lang_buttons[3:],
    ]

    pitch_label = "פעיל" if pitch_match_on else "כבוי"
    await update.message.reply_text(
        f"הגדרות נוכחיות:\n"
        f"התאמת גובה (STS): {pitch_label}\n"
        f"מהירות: {settings['speed']}x\n"
        f"שפה: {lang_name}\n\n"
        "התאמת גובה = משווה את גובה הקול שלך לקול היעד לפני המרה (חשוב במיוחד כשממירים בין גבר לאישה).\n\n"
        "בחר/י להגדיר:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_set_speed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    speed = float(query.data.replace("set_speed:", ""))
    await set_user_settings(query.from_user.id, speed=speed)
    await query.edit_message_text(f"מהירות הדיבור הוגדרה ל-{speed}x")


async def handle_set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = query.data.replace("set_lang:", "")
    lang_name = LANG_OPTIONS.get(lang, lang)
    await set_user_settings(query.from_user.id, language=lang)
    await query.edit_message_text(f"השפה הוגדרה ל-{lang_name}")


async def handle_set_pitch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    value = query.data.replace("set_pitch:", "")
    enabled = value == "on"
    await set_user_pitch_match(query.from_user.id, enabled)
    if enabled:
        await query.edit_message_text(
            "התאמת גובה הופעלה.\n"
            "בהמרת הקלטה (STS), הקלט שלך יוסט אוטומטית לגובה ממוצע של קול היעד "
            "כדי שהתוצאה תישמע טבעית יותר. פעולה זו מתבצעת רק אם ההפרש משמעותי."
        )
    else:
        await query.edit_message_text(
            "התאמת גובה כובתה. הקלטות יישלחו ל-STS כפי שהן."
        )


# ── /enhance — remix a voice with a prompt ────────────────────────────────────

async def cmd_enhance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return ConversationHandler.END

    custom_voices = await get_user_voices(user_id)
    if not custom_voices:
        await update.message.reply_text(
            "אין לך קולות מותאמים לשיפור.\n"
            "צור/י קול חדש עם /newvoice קודם."
        )
        return ConversationHandler.END

    buttons = []
    for v in custom_voices:
        buttons.append([InlineKeyboardButton(v["name"], callback_data=f"enhance_pick:{v['id']}")])

    await update.message.reply_text(
        "בחר/י קול לשיפור:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SELECTING_ENHANCE_VOICE


async def handle_enhance_voice_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    voice_doc_id = query.data.replace("enhance_pick:", "")
    voice = await get_voice_by_id(voice_doc_id)
    if not voice:
        await query.edit_message_text("הקול לא נמצא.")
        return ConversationHandler.END

    context.user_data["enhance_voice"] = voice
    await query.edit_message_text(
        f"קול נבחר: {voice['name']}\n\n"
        "כתוב/י הנחיה באנגלית לשיפור הקול.\n\n"
        "דוגמאות:\n"
        "Make sure she always speaks in Israeli accent.\n"
        "Make the voice warmer and more intimate.\n"
        "Add a slight raspy quality to the voice.\n"
        "Make the pitch slightly higher.\n\n"
        "שלח/י /cancel לביטול."
    )
    return WRITING_ENHANCE_PROMPT


async def handle_enhance_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("שלח/י הנחיה לשיפור.")
        return WRITING_ENHANCE_PROMPT

    voice = context.user_data.get("enhance_voice")
    if not voice:
        await update.message.reply_text("שגיאה. נסה/י שוב עם /enhance")
        return ConversationHandler.END

    await update.message.reply_text(f"משפר את \"{voice['name']}\"... זה עלול לקחת רגע.")
    await update.message.reply_chat_action("typing")

    try:
        previews = remix_voice(voice["elevenlabs_voice_id"], prompt)
        if not previews:
            await update.message.reply_text("השיפור לא הצליח. נסה/י הנחיה אחרת.")
            return ConversationHandler.END

        context.user_data["enhance_previews"] = previews
        context.user_data["enhance_prompt"] = prompt

        import base64
        for i, p in enumerate(previews):
            audio_bytes = base64.b64decode(p["audio_base64"])
            ogg = mp3_to_ogg_opus(audio_bytes)
            await update.message.reply_voice(voice=ogg, caption=f"גרסה {i+1}")

        buttons = []
        for i in range(len(previews)):
            buttons.append([InlineKeyboardButton(f"שמור גרסה {i+1}", callback_data=f"enhance_save:{i}")])
        buttons.append([InlineKeyboardButton("ביטול", callback_data="enhance_save:cancel")])

        await update.message.reply_text(
            "בחר/י את הגרסה שאהבת:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return CONFIRMING_ENHANCE

    except Exception:
        logger.exception("Voice enhance failed")
        await update.message.reply_text("השיפור נכשל. נסה/י שוב.")
        return ConversationHandler.END


async def handle_enhance_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data.replace("enhance_save:", "")
    if data == "cancel":
        await query.edit_message_text("השיפור בוטל.")
        context.user_data.pop("enhance_voice", None)
        context.user_data.pop("enhance_previews", None)
        context.user_data.pop("enhance_prompt", None)
        return ConversationHandler.END

    idx = int(data)
    previews = context.user_data.get("enhance_previews", [])
    original_voice = context.user_data.get("enhance_voice", {})
    prompt = context.user_data.get("enhance_prompt", "")

    if idx >= len(previews):
        await query.edit_message_text("גרסה לא נמצאה.")
        return ConversationHandler.END

    generated_voice_id = previews[idx]["generated_voice_id"]
    user_id = query.from_user.id
    new_name = f"{original_voice.get('name', 'Enhanced')} (enhanced)"

    try:
        client = _get_elevenlabs()
        saved_voice = client.text_to_voice.create(
            voice_name=new_name,
            voice_description=prompt,
            generated_voice_id=generated_voice_id,
        )
        real_voice_id = saved_voice.voice_id
        logger.info("Saved enhanced voice: %s -> %s", generated_voice_id, real_voice_id)

        voice_doc_id = await create_voice(user_id, new_name, real_voice_id, [])
        await set_active_voice(user_id, voice_doc_id)

        await query.edit_message_text(
            f"הקול \"{new_name}\" נשמר והוגדר כפעיל!\n"
            "השתמש/י ב-/voices כדי לעבור בין קולות."
        )
    except Exception:
        logger.exception("Failed to save enhanced voice")
        await query.edit_message_text("שמירת הקול נכשלה. נסה/י שוב.")

    context.user_data.pop("enhance_voice", None)
    context.user_data.pop("enhance_previews", None)
    context.user_data.pop("enhance_prompt", None)
    return ConversationHandler.END


async def enhance_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("enhance_voice", None)
    context.user_data.pop("enhance_previews", None)
    context.user_data.pop("enhance_prompt", None)
    await update.message.reply_text("השיפור בוטל.")
    return ConversationHandler.END


# ── /effects — background sound effects ───────────────────────────────────────

async def cmd_effects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return ConversationHandler.END

    current = await get_user_effect(user_id)
    status = f"אפקט נוכחי: {current}" if current else "אין אפקט רקע פעיל."
    await update.message.reply_text(
        f"{status}\n\n"
        "שלח/י תיאור של אפקט הרקע באנגלית, או /cancel לביטול.\n\n"
        "דוגמאות:\n"
        "shower water running\n"
        "loud club music with bass\n"
        "dogs barking in background\n"
        "rain and thunder\n"
        "busy cafe ambient noise"
    )
    return WAITING_EFFECT


async def effect_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    effect = update.message.text.strip()
    if not effect:
        await update.message.reply_text("שלח/י תיאור של אפקט.")
        return WAITING_EFFECT

    await set_user_effect(user_id, effect)
    await update.message.reply_text(f"אפקט רקע הוגדר: {effect}")
    return ConversationHandler.END


async def effect_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("הגדרת אפקט בוטלה.")
    return ConversationHandler.END


async def cmd_noeffects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return
    await set_user_effect(user_id, None)
    await update.message.reply_text("אפקט הרקע הוסר.")


# ── Main ──────────────────────────────────────────────────────────────────────

async def post_init(application) -> None:
    logger.info("Bot initialized")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in .env")
        return

    if not ELEVENLABS_API_KEY:
        print("ELEVENLABS_API_KEY not found in .env")
        return

    if not PLAYAI_API_KEY or not PLAYAI_USER_ID:
        logger.warning("PLAYAI_API_KEY or PLAYAI_USER_ID not set — TTS will fall back to ElevenLabs")
    elif not PLAYAI_DEFAULT_VOICE_ID:
        logger.warning("PLAYAI_DEFAULT_VOICE_ID not set — default voice TTS may fail")

    app = Application.builder().token(token).post_init(post_init).build()

    newvoice_conv = ConversationHandler(
        entry_points=[CommandHandler("newvoice", newvoice_start)],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newvoice_name)],
            COLLECTING_SAMPLES: [
                MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.ALL, newvoice_sample),
                CommandHandler("done", newvoice_done),
            ],
        },
        fallbacks=[CommandHandler("cancel", newvoice_cancel)],
    )

    prompt_conv = ConversationHandler(
        entry_points=[CommandHandler("prompt", cmd_prompt)],
        states={
            WAITING_PROMPT: [
                CommandHandler("reset", prompt_reset),
                MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_set),
            ],
        },
        fallbacks=[CommandHandler("cancel", prompt_cancel)],
    )

    effects_conv = ConversationHandler(
        entry_points=[CommandHandler("effects", cmd_effects)],
        states={
            WAITING_EFFECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, effect_set),
            ],
        },
        fallbacks=[CommandHandler("cancel", effect_cancel)],
    )

    dialogue_conv = ConversationHandler(
        entry_points=[CommandHandler("dialogue", cmd_dialogue)],
        states={
            SELECTING_DIALOGUE_VOICES: [
                CallbackQueryHandler(handle_dialogue_voice_pick, pattern=r"^dlg_pick:"),
            ],
            CHOOSING_DIALOGUE_MODE: [
                CallbackQueryHandler(handle_dialogue_mode, pattern=r"^dlg_mode:"),
            ],
            WRITING_DIALOGUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dialogue_text),
            ],
            RECORDING_DIALOGUE_STS: [
                MessageHandler(filters.VOICE | filters.AUDIO, handle_dialogue_sts_recording),
                CommandHandler("done", handle_dialogue_sts_done),
            ],
            PICKING_DIALOGUE_VOICE_FOR_TURN: [
                CallbackQueryHandler(handle_dialogue_sts_voice_pick, pattern=r"^dlg_sts_voice:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", dialogue_cancel)],
        per_message=False,
        per_chat=True,
    )

    enhance_conv = ConversationHandler(
        entry_points=[CommandHandler("enhance", cmd_enhance)],
        states={
            SELECTING_ENHANCE_VOICE: [
                CallbackQueryHandler(handle_enhance_voice_pick, pattern=r"^enhance_pick:"),
            ],
            WRITING_ENHANCE_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_enhance_prompt),
            ],
            CONFIRMING_ENHANCE: [
                CallbackQueryHandler(handle_enhance_save, pattern=r"^enhance_save:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", enhance_cancel)],
        per_message=False,
        per_chat=True,
    )

    app.add_handler(newvoice_conv)
    app.add_handler(prompt_conv)
    app.add_handler(effects_conv)
    app.add_handler(dialogue_conv)
    app.add_handler(enhance_conv)
    app.add_handler(CommandHandler("noeffects", cmd_noeffects))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CallbackQueryHandler(handle_set_speed, pattern=r"^set_speed:"))
    app.add_handler(CallbackQueryHandler(handle_set_lang, pattern=r"^set_lang:"))
    app.add_handler(CallbackQueryHandler(handle_set_pitch, pattern=r"^set_pitch:"))
    app.add_handler(CommandHandler("voices", cmd_voices))
    app.add_handler(CommandHandler("deletevoice", cmd_deletevoice))
    app.add_handler(CallbackQueryHandler(handle_voice_select, pattern=r"^voice_select:"))
    app.add_handler(CallbackQueryHandler(handle_voice_delete, pattern=r"^voice_delete:"))
    app.add_handler(CallbackQueryHandler(handle_voice_tune, pattern=r"^voice_tune:"))
    app.add_handler(CallbackQueryHandler(handle_voice_tune_set, pattern=r"^voice_tune_set:"))
    app.add_handler(CallbackQueryHandler(handle_voice_tune_reset, pattern=r"^voice_tune_reset:"))
    app.add_handler(CallbackQueryHandler(handle_voice_tune_close, pattern=r"^voice_tune_close$"))
    app.add_handler(CallbackQueryHandler(handle_voice_tune_noop, pattern=r"^voice_tune_noop$"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot started")
    print(f"  Text -> TTS ({TTS_MODEL})")
    print(f"  Voice -> STS ({STS_MODEL})")
    print("Ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
