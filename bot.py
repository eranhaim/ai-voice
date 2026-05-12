import os
import logging
import subprocess
import uuid
from io import BytesIO

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
    seed_system_voices,
)
from s3 import upload_sample, upload_run_audio, delete_samples

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

DEFAULT_AUDIO_TAG = "[flirty CASUAL ISRAELI ACCENT girl, addressing a male]"
MIN_SAMPLE_DURATION = 5

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


def text_to_speech(text: str, voice_id: str, audio_tag: str = "", speed: float = 1.0, language: str = "he") -> bytes:
    client = _get_elevenlabs()
    audio_tag = _ensure_brackets(audio_tag) if audio_tag else ""
    tagged_text = f"{audio_tag} {text}".strip() if audio_tag else text
    voice_settings = {"stability": 0.7, "similarity_boost": 0.75, "style": 0.0, "speed": speed}
    audio_iter = client.text_to_speech.convert(
        text=tagged_text,
        voice_id=voice_id,
        model_id=TTS_MODEL,
        output_format="mp3_44100_128",
        language_code=language,
        voice_settings=voice_settings,
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
        voice_settings='{"stability": 0.5, "similarity_boost": 0.8, "style": 0.0}',
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
        output_format="mp3_44100_128",
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
        "/voices — בחירת קול פעיל\n"
        "/newvoice — יצירת קול חדש מהקלטות\n"
        "/deletevoice — מחיקת קול מותאם\n"
        "/prompt — הגדרת סגנון הדיבור\n"
        "/settings — מהירות דיבור ושפה\n"
        "/enhance — שיפור קול קיים עם הנחיה\n"
        "/dialogue — יצירת שיחה עם מספר קולות\n"
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

    voice_id = await get_user_voice_id(user_id)
    vname = await get_voice_name(user_id)
    audio_tag = await get_user_prompt(user_id) or DEFAULT_AUDIO_TAG
    effect = await get_user_effect(user_id)
    settings = await get_user_settings(user_id)
    logger.info("TTS from %d with voice %s: %d chars", user_id, voice_id, len(text))
    await update.message.reply_chat_action("record_voice")

    try:
        audio_data = text_to_speech(text, voice_id, audio_tag, settings["speed"], settings["language"])
        if not audio_data:
            logger.warning("TTS returned empty audio, retrying without audio tag")
            audio_data = text_to_speech(text, voice_id, speed=settings["speed"], language=settings["language"])
        if not audio_data:
            await update.message.reply_text("לא הצלחתי ליצור הקלטה. נסה/י שוב.")
            return
        ogg_data = process_audio_with_effect(audio_data, effect)
        logger.info("TTS done: %d bytes -> %d bytes ogg", len(audio_data), len(ogg_data))
        await update.message.reply_voice(voice=ogg_data)
        await log_run(user_id, "tts", text, vname)
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

    voice_id = await get_user_voice_id(user_id)
    vname = await get_voice_name(user_id)
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

        converted = speech_to_speech(audio_bytes, voice_id)
        ogg_data = process_audio_with_effect(converted, effect)
        logger.info("STS done: %d bytes -> %d bytes ogg", len(converted), len(ogg_data))

        await update.message.reply_voice(voice=ogg_data)
        await log_run(user_id, "sts", transcription, vname, input_audio_url)
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
    system_voices = await get_system_voices()
    custom_voices = await get_user_voices(user_id)

    buttons = []
    for sv in system_voices:
        is_active = sv["elevenlabs_voice_id"] == current_voice_id
        label = (">> " if is_active else "") + sv["name"]
        buttons.append([InlineKeyboardButton(label, callback_data=f"voice_select:{sv['id']}")])

    for v in custom_voices:
        is_active = v["elevenlabs_voice_id"] == current_voice_id
        label = (">> " if is_active else "") + v["name"]
        buttons.append([InlineKeyboardButton(label, callback_data=f"voice_select:{v['id']}")])

    await update.message.reply_text(
        "בחר/י קול פעיל:\n(>> מסמן את הנוכחי)",
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


async def newvoice_sample(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    voice = update.message.voice or update.message.audio
    if not voice and update.message.document:
        mime = update.message.document.mime_type or ""
        if mime.startswith("audio/") or mime.startswith("video/"):
            voice = update.message.document
    if not voice:
        await update.message.reply_text("שלח/י הקלטה קולית, /done לסיום, או /cancel לביטול.")
        return COLLECTING_SAMPLES

    if getattr(voice, "duration", None) and voice.duration < MIN_SAMPLE_DURATION:
        await update.message.reply_text(
            f"ההקלטה קצרה מדי ({voice.duration} שניות). "
            f"כל הקלטה חייבת להיות לפחות {MIN_SAMPLE_DURATION} שניות."
        )
        return COLLECTING_SAMPLES

    samples = context.user_data.get("new_voice_samples", [])
    if len(samples) >= 25:
        await update.message.reply_text("הגעת למקסימום 25 דגימות. שלח/י /done ליצירת הקול.")
        return COLLECTING_SAMPLES

    file = await context.bot.get_file(voice.file_id)
    data = await file.download_as_bytearray()
    samples.append(bytes(data))
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


async def newvoice_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    samples = context.user_data.get("new_voice_samples", [])
    name = context.user_data.get("new_voice_name", "ללא שם")

    if not samples:
        await update.message.reply_text("לא התקבלו דגימות. שלח/י לפחות הקלטה אחת.")
        return COLLECTING_SAMPLES

    await update.message.reply_text(f"יוצר את הקול \"{name}\" מ-{len(samples)} דגימה/ות... זה עלול לקחת רגע.")
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

    context.user_data.pop("new_voice_name", None)
    context.user_data.pop("new_voice_samples", None)
    return ConversationHandler.END


async def newvoice_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_voice_name", None)
    context.user_data.pop("new_voice_samples", None)
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
            voice_id = voices[turn["voice_idx"]]["elevenlabs_voice_id"]
            converted = speech_to_speech(turn["audio"], voice_id)
            converted_clips.append(converted)

        stitched = stitch_audio_clips(converted_clips)
        effect = await get_user_effect(user_id)
        ogg_data = process_audio_with_effect(stitched, effect)
        logger.info("STS Dialogue done: %d turns, %d bytes", len(turns), len(ogg_data))

        await update.message.reply_voice(voice=ogg_data)
        voice_names = ", ".join(voices[t["voice_idx"]]["name"] for t in turns)
        await log_run(user_id, "dialogue_sts", f"{len(turns)} turns", voice_names)
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
        await log_run(user_id, "dialogue", dialogue_text, voice_names)
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
    lang_name = LANG_OPTIONS.get(settings["language"], settings["language"])

    speed_buttons = []
    for s in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
        label = f"{'>> ' if settings['speed'] == s else ''}{s}x"
        speed_buttons.append(InlineKeyboardButton(label, callback_data=f"set_speed:{s}"))

    lang_buttons = []
    for code, name in LANG_OPTIONS.items():
        label = f"{'>> ' if settings['language'] == code else ''}{name}"
        lang_buttons.append(InlineKeyboardButton(label, callback_data=f"set_lang:{code}"))

    buttons = [speed_buttons[:3], speed_buttons[3:], lang_buttons[:3], lang_buttons[3:]]

    await update.message.reply_text(
        f"הגדרות נוכחיות:\n"
        f"מהירות: {settings['speed']}x\n"
        f"שפה: {lang_name}\n\n"
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
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("noeffects", cmd_noeffects))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CallbackQueryHandler(handle_set_speed, pattern=r"^set_speed:"))
    app.add_handler(CallbackQueryHandler(handle_set_lang, pattern=r"^set_lang:"))
    app.add_handler(CommandHandler("voices", cmd_voices))
    app.add_handler(CommandHandler("deletevoice", cmd_deletevoice))
    app.add_handler(CallbackQueryHandler(handle_voice_select, pattern=r"^voice_select:"))
    app.add_handler(CallbackQueryHandler(handle_voice_delete, pattern=r"^voice_delete:"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot started")
    print(f"  Text -> TTS ({TTS_MODEL})")
    print(f"  Voice -> STS ({STS_MODEL})")
    print("Ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
