import os
import asyncio
import logging
from io import BytesIO

import httpx
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

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "jqcCZkN6Knx8BJ5TBdYR")

TTS_MODEL = "eleven_v3"
STS_MODEL = "eleven_multilingual_sts_v2"


def _get_client() -> ElevenLabs:
    return ElevenLabs(api_key=ELEVENLABS_API_KEY)


def text_to_speech(text: str) -> bytes:
    client = _get_client()
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
    client = _get_client()
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Voice Bot\n"
        "━━━━━━━━━━\n\n"
        "Two modes:\n\n"
        "1. Send TEXT — I'll speak it as a female voice (Hebrew TTS)\n"
        "2. Send a VOICE MESSAGE — I'll convert it to a female voice\n"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if not text or not text.strip():
        return

    if len(text) > 5000:
        await update.message.reply_text("Text too long. Max 5,000 characters.")
        return

    logger.info("TTS request: %d chars", len(text))
    await update.message.reply_chat_action("record_voice")

    try:
        audio_data = text_to_speech(text)
        logger.info("TTS done: %d bytes", len(audio_data))
        await update.message.reply_voice(voice=audio_data)
    except Exception:
        logger.exception("TTS failed")
        await update.message.reply_text("Something went wrong. Please try again.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    logger.info("STS request: duration=%ss, size=%s bytes", voice.duration, voice.file_size)
    await update.message.reply_chat_action("record_voice")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_data = await file.download_as_bytearray()
        logger.info("Downloaded %d bytes, converting voice...", len(audio_data))

        converted = speech_to_speech(bytes(audio_data))
        logger.info("STS done: %d bytes", len(converted))

        await update.message.reply_voice(voice=converted)
    except Exception:
        logger.exception("STS failed")
        await update.message.reply_text("Something went wrong. Please try again.")


def force_clear_session(token: str) -> None:
    """Kill any existing polling sessions before we start ours."""
    api = f"https://api.telegram.org/bot{token}"
    with httpx.Client() as client:
        client.post(f"{api}/deleteWebhook", params={"drop_pending_updates": "true"})
        client.post(f"{api}/getUpdates", json={"offset": -1, "timeout": 0})
    logger.info("Cleared existing Telegram sessions, waiting 5s...")
    import time
    time.sleep(5)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in .env")
        return

    if not ELEVENLABS_API_KEY:
        print("ELEVENLABS_API_KEY not found in .env")
        print("Get a free key at: https://elevenlabs.io")
        return

    force_clear_session(token)

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
