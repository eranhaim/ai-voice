import os
import logging
from io import BytesIO

from openai import OpenAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from db import is_authorized, log_run

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "jqcCZkN6Knx8BJ5TBdYR")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

TTS_MODEL = "eleven_v3"
STS_MODEL = "eleven_multilingual_sts_v2"

UNAUTHORIZED_MSG = "You are not authorized to use this bot."


def _get_elevenlabs() -> ElevenLabs:
    return ElevenLabs(api_key=ELEVENLABS_API_KEY)


def _get_openai() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


def text_to_speech(text: str) -> bytes:
    client = _get_elevenlabs()
    audio_iter = client.text_to_speech.convert(
        text=text,
        voice_id=VOICE_ID,
        model_id=TTS_MODEL,
        output_format="mp3_44100_128",
        language_code="he",
    )
    buffer = BytesIO()
    for chunk in audio_iter:
        buffer.write(chunk)
    return buffer.getvalue()


def speech_to_speech(audio_bytes: bytes) -> bytes:
    client = _get_elevenlabs()
    audio_iter = client.speech_to_speech.convert(
        voice_id=VOICE_ID,
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update.effective_user.id):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    await update.message.reply_text(
        "Voice Bot\n"
        "━━━━━━━━━━\n\n"
        "Two modes:\n\n"
        "1. Send TEXT — I'll speak it as a female voice (Hebrew TTS)\n"
        "2. Send a VOICE MESSAGE — I'll convert it to a female voice\n"
    )


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

    logger.info("TTS request from %d: %d chars", user_id, len(text))
    await update.message.reply_chat_action("record_voice")

    try:
        audio_data = text_to_speech(text)
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

    logger.info("STS request from %d: duration=%ss", user_id, voice.duration)
    await update.message.reply_chat_action("record_voice")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_data = await file.download_as_bytearray()
        audio_bytes = bytes(audio_data)
        logger.info("Downloaded %d bytes", len(audio_bytes))

        transcription = ""
        try:
            transcription = transcribe(audio_bytes)
            logger.info("Transcription: %s", transcription[:100])
        except Exception:
            logger.exception("Transcription failed, continuing with voice conversion")

        converted = speech_to_speech(audio_bytes)
        logger.info("STS done: %d bytes", len(converted))

        await update.message.reply_voice(voice=converted)
        await log_run(user_id, "sts", transcription)
    except Exception:
        logger.exception("STS failed")
        await update.message.reply_text("Something went wrong. Please try again.")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in .env")
        return

    if not ELEVENLABS_API_KEY:
        print("ELEVENLABS_API_KEY not found in .env")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"Bot started — voice: {VOICE_ID}")
    print(f"  Text -> TTS ({TTS_MODEL})")
    print(f"  Voice -> STS ({STS_MODEL})")
    print("Ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
