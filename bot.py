import os
import logging
import uuid
from io import BytesIO

from openai import OpenAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultVoice
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    InlineQueryHandler,
    filters,
    ContextTypes,
)

from db import (
    is_authorized,
    log_run,
    get_user_voice_id,
    set_active_voice,
    create_voice,
    get_user_voices,
    get_voice_by_id,
    delete_voice,
)
from s3 import upload_sample, delete_samples

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

TTS_MODEL = "eleven_v3"
STS_MODEL = "eleven_multilingual_sts_v2"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
API_INTERNAL_URL = "http://api:8000"

UNAUTHORIZED_MSG = "You are not authorized to use this bot."

WAITING_NAME, COLLECTING_SAMPLES = range(2)


def _get_elevenlabs() -> ElevenLabs:
    return ElevenLabs(api_key=ELEVENLABS_API_KEY)


def _get_openai() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


# ── ElevenLabs operations ────────────────────────────────────────────────────

def text_to_speech(text: str, voice_id: str) -> bytes:
    client = _get_elevenlabs()
    audio_iter = client.text_to_speech.convert(
        text=text,
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


def clone_voice(name: str, audio_files: list[bytes]) -> str:
    """Clone a voice via ElevenLabs IVC and return the new voice_id."""
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


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update.effective_user.id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    await update.message.reply_text(
        "Voice Bot\n"
        "━━━━━━━━━━\n\n"
        "Send TEXT or a VOICE MESSAGE and I'll convert it.\n\n"
        "Voice commands:\n"
        "/voices — choose your active voice\n"
        "/newvoice — create a voice from recordings\n"
        "/deletevoice — remove a custom voice\n"
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
        await update.message.reply_text("Text too long. Max 5,000 characters.")
        return

    voice_id = await get_user_voice_id(user_id)
    logger.info("TTS from %d with voice %s: %d chars", user_id, voice_id, len(text))
    await update.message.reply_chat_action("record_voice")

    try:
        audio_data = text_to_speech(text, voice_id)
        logger.info("TTS done: %d bytes", len(audio_data))
        await update.message.reply_voice(voice=audio_data)
        await log_run(user_id, "tts", text)
    except Exception:
        logger.exception("TTS failed")
        await update.message.reply_text("Something went wrong. Please try again.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    voice_id = await get_user_voice_id(user_id)
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
        logger.info("STS done: %d bytes", len(converted))

        await update.message.reply_voice(voice=converted)
        await log_run(user_id, "sts", transcription)
    except Exception:
        logger.exception("STS failed")
        await update.message.reply_text("Something went wrong. Please try again.")


# ── /voices — select active voice ────────────────────────────────────────────

async def cmd_voices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    current_voice_id = await get_user_voice_id(user_id)
    voices = await get_user_voices(user_id)

    is_default = current_voice_id == DEFAULT_VOICE_ID
    default_label = (">> " if is_default else "") + "Default Voice"
    buttons = [[InlineKeyboardButton(default_label, callback_data="voice_select:default")]]

    for v in voices:
        is_active = v["elevenlabs_voice_id"] == current_voice_id
        label = (">> " if is_active else "") + v["name"]
        buttons.append([InlineKeyboardButton(label, callback_data=f"voice_select:{v['id']}")])

    await update.message.reply_text(
        "Select your active voice:\n(>> marks current)",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_voice_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data.replace("voice_select:", "")

    if data == "default":
        await set_active_voice(user_id, None)
        await query.edit_message_text("Switched to Default Voice.")
    else:
        voice = await get_voice_by_id(data)
        if not voice:
            await query.edit_message_text("Voice not found.")
            return
        await set_active_voice(user_id, data)
        await query.edit_message_text(f"Switched to: {voice['name']}")


# ── /deletevoice — remove a custom voice ─────────────────────────────────────

async def cmd_deletevoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    voices = await get_user_voices(user_id)
    if not voices:
        await update.message.reply_text("You have no custom voices.")
        return

    buttons = []
    for v in voices:
        buttons.append([InlineKeyboardButton(f"Delete: {v['name']}", callback_data=f"voice_delete:{v['id']}")])

    await update.message.reply_text(
        "Which voice do you want to delete?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_voice_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    voice_doc_id = query.data.replace("voice_delete:", "")
    deleted = await delete_voice(voice_doc_id)

    if not deleted:
        await query.edit_message_text("Voice not found.")
        return

    delete_elevenlabs_voice(deleted["elevenlabs_voice_id"])
    try:
        delete_samples(deleted["sample_urls"])
    except Exception:
        logger.exception("Failed to delete S3 samples")

    await query.edit_message_text("Voice deleted.")


# ── /newvoice — create a cloned voice ────────────────────────────────────────

async def newvoice_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return ConversationHandler.END

    await update.message.reply_text("What name do you want for this voice?")
    return WAITING_NAME


async def newvoice_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Please send a name.")
        return WAITING_NAME

    context.user_data["new_voice_name"] = name
    context.user_data["new_voice_samples"] = []

    await update.message.reply_text(
        f"Voice name: {name}\n\n"
        "Now send me 1-5 voice recordings of this person speaking.\n"
        "Send /done when finished, or /cancel to abort."
    )
    return COLLECTING_SAMPLES


async def newvoice_sample(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    voice = update.message.voice or update.message.audio
    if not voice:
        await update.message.reply_text("Please send a voice recording, /done to finish, or /cancel to abort.")
        return COLLECTING_SAMPLES

    samples = context.user_data.get("new_voice_samples", [])
    if len(samples) >= 5:
        await update.message.reply_text("Maximum 5 samples. Send /done to finish.")
        return COLLECTING_SAMPLES

    file = await context.bot.get_file(voice.file_id)
    data = await file.download_as_bytearray()
    samples.append(bytes(data))
    context.user_data["new_voice_samples"] = samples

    await update.message.reply_text(
        f"Sample {len(samples)} received. "
        f"Send more or /done to create the voice ({len(samples)}/5)."
    )
    return COLLECTING_SAMPLES


async def newvoice_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    samples = context.user_data.get("new_voice_samples", [])
    name = context.user_data.get("new_voice_name", "Unnamed")

    if not samples:
        await update.message.reply_text("No samples received. Send at least one voice recording.")
        return COLLECTING_SAMPLES

    await update.message.reply_text(f"Creating voice \"{name}\" from {len(samples)} sample(s)... This may take a moment.")
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
            f"Voice \"{name}\" created and set as active!\n"
            "Use /voices to switch between voices."
        )
    except Exception:
        logger.exception("Voice creation failed")
        await update.message.reply_text("Failed to create voice. Please try again.")

    context.user_data.pop("new_voice_name", None)
    context.user_data.pop("new_voice_samples", None)
    return ConversationHandler.END


async def newvoice_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_voice_name", None)
    context.user_data.pop("new_voice_samples", None)
    await update.message.reply_text("Voice creation cancelled.")
    return ConversationHandler.END


# ── Inline mode ──────────────────────────────────────────────────────────────

async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    user_id = query.from_user.id
    text = query.query.strip()

    if not text:
        return

    if not await is_authorized(user_id):
        return

    try:
        voice_id = await get_user_voice_id(user_id)
        audio_data = text_to_speech(text, voice_id)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_INTERNAL_URL}/api/audio/upload",
                content=audio_data,
            )
            resp.raise_for_status()
            audio_id = resp.json()["audio_id"]

        voice_url = f"{PUBLIC_BASE_URL}/audio/{audio_id}"

        results = [
            InlineQueryResultVoice(
                id=audio_id,
                voice_url=voice_url,
                title="Send voice message",
            )
        ]
        await query.answer(results, cache_time=0)
        await log_run(user_id, "inline_tts", text)

    except Exception:
        logger.exception("Inline TTS failed for user %d", user_id)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in .env")
        return

    if not ELEVENLABS_API_KEY:
        print("ELEVENLABS_API_KEY not found in .env")
        return

    app = Application.builder().token(token).build()

    newvoice_conv = ConversationHandler(
        entry_points=[CommandHandler("newvoice", newvoice_start)],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newvoice_name)],
            COLLECTING_SAMPLES: [
                MessageHandler(filters.VOICE | filters.AUDIO, newvoice_sample),
                CommandHandler("done", newvoice_done),
            ],
        },
        fallbacks=[CommandHandler("cancel", newvoice_cancel)],
    )

    app.add_handler(newvoice_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("voices", cmd_voices))
    app.add_handler(CommandHandler("deletevoice", cmd_deletevoice))
    app.add_handler(CallbackQueryHandler(handle_voice_select, pattern=r"^voice_select:"))
    app.add_handler(CallbackQueryHandler(handle_voice_delete, pattern=r"^voice_delete:"))
    app.add_handler(InlineQueryHandler(handle_inline_query))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"Bot started — default voice: {DEFAULT_VOICE_ID}")
    print(f"  Text -> TTS ({TTS_MODEL})")
    print(f"  Voice -> STS ({STS_MODEL})")
    print("Ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
