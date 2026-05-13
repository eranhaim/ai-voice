import os
import logging
import subprocess
from io import BytesIO

from openai import OpenAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

from db import (
    is_authorized,
    log_run,
    get_user_prompt,
    set_user_prompt,
    get_user_effect,
    set_user_effect,
    seed_system_voices,
    DEFAULT_VOICE_ID,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

TTS_MODEL = "eleven_v3"
STS_MODEL = "eleven_multilingual_sts_v2"

DEFAULT_AUDIO_TAG = "[flirty, speaking to a man]"

WAITING_PROMPT = 10
WAITING_EFFECT = 11

UNAUTHORIZED_MSG = "אין לך הרשאה להשתמש בבוט הזה."


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


def text_to_speech(text: str, voice_id: str, audio_tag: str = "") -> bytes:
    client = _get_elevenlabs()
    audio_tag = _ensure_brackets(audio_tag) if audio_tag else ""
    tagged_text = f"{audio_tag} {text}".strip() if audio_tag else text
    audio_iter = client.text_to_speech.convert(
        text=tagged_text,
        voice_id=voice_id,
        model_id=TTS_MODEL,
        output_format="mp3_44100_128",
        language_code="he",
    )
    buffer = BytesIO()
    for chunk in audio_iter:
        buffer.write(chunk)
    return buffer.getvalue()


def speech_to_speech(audio_bytes: bytes, voice_id: str) -> bytes:
    client = _get_elevenlabs()
    audio_iter = client.speech_to_speech.convert(
        voice_id=voice_id,
        audio=BytesIO(audio_bytes),
        model_id=STS_MODEL,
        output_format="mp3_44100_128",
        voice_settings='{"stability": 0.8, "similarity_boost": 0.95, "style": 0.0}',
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


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update.effective_user.id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    await update.message.reply_text(
        "בוט קולי\n"
        "━━━━━━━━━━\n\n"
        "שלח/י טקסט או הודעה קולית ואני אמיר אותם.\n\n"
        "פקודות:\n"
        "/prompt — הגדרת סגנון הדיבור\n"
        "/effects — הוספת אפקט קולי ברקע\n"
        "/noeffects — הסרת אפקט הרקע\n"
    )


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

    voice_id = DEFAULT_VOICE_ID
    audio_tag = await get_user_prompt(user_id) or DEFAULT_AUDIO_TAG
    effect = await get_user_effect(user_id)
    logger.info("TTS from %d with voice %s: %d chars", user_id, voice_id, len(text))
    await update.message.reply_chat_action("record_voice")

    try:
        audio_data = text_to_speech(text, voice_id, audio_tag)
        if not audio_data:
            logger.warning("TTS returned empty audio, retrying without audio tag")
            audio_data = text_to_speech(text, voice_id)
        if not audio_data:
            await update.message.reply_text("לא הצלחתי ליצור הקלטה. נסה/י שוב.")
            return
        ogg_data = process_audio_with_effect(audio_data, effect)
        logger.info("TTS done: %d bytes -> %d bytes ogg", len(audio_data), len(ogg_data))
        await update.message.reply_voice(voice=ogg_data)
        await log_run(user_id, "tts", text)
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

    voice_id = DEFAULT_VOICE_ID
    effect = await get_user_effect(user_id)
    logger.info("STS from %d with voice %s: duration=%ss", user_id, voice_id, voice.duration)
    await update.message.reply_chat_action("record_voice")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_data = await file.download_as_bytearray()
        audio_bytes = bytes(audio_data)

        transcription = ""
        try:
            transcription = transcribe(audio_bytes)
            logger.info("Transcription: %s", transcription[:100])
        except Exception:
            logger.exception("Transcription failed, continuing with voice conversion")

        converted = speech_to_speech(audio_bytes, voice_id)
        ogg_data = process_audio_with_effect(converted, effect)
        logger.info("STS done: %d bytes -> %d bytes ogg", len(converted), len(ogg_data))

        await update.message.reply_voice(voice=ogg_data)
        await log_run(user_id, "sts", transcription)
    except Exception:
        logger.exception("STS failed")
        await update.message.reply_text("משהו השתבש. נסה/י שוב.")


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
        "דוגמאות:\n"
        "[flirty, speaking to a man]\n"
        "[warm, gentle, romantic]\n"
        "[playful, teasing, seductive]"
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
    await seed_system_voices()
    logger.info("System voices seeded")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in .env")
        return

    if not ELEVENLABS_API_KEY:
        print("ELEVENLABS_API_KEY not found in .env")
        return

    app = Application.builder().token(token).post_init(post_init).build()

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

    app.add_handler(prompt_conv)
    app.add_handler(effects_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("noeffects", cmd_noeffects))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot started")
    print(f"  Text -> TTS ({TTS_MODEL})")
    print(f"  Voice -> STS ({STS_MODEL})")
    print("Ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
