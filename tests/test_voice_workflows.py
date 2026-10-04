import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    import api
    import bot
except ModuleNotFoundError:
    api = None
    bot = None


class _Collection:
    def __init__(self):
        self.documents = []

    async def find_one(self, query):
        for document in self.documents:
            if document.get("elevenlabs_voice_id") == query.get("elevenlabs_voice_id"):
                return document
        return None

    async def insert_one(self, document):
        saved = dict(document)
        saved["_id"] = f"voice-{len(self.documents) + 1}"
        self.documents.append(saved)
        return SimpleNamespace(inserted_id=saved["_id"])


class _Database:
    def __init__(self):
        self.system_voices = _Collection()


class _ClonedVoice:
    voice_id = "cloned-voice-id"


class _IVC:
    def create(self, **kwargs):
        return _ClonedVoice()


class _ElevenLabs:
    def __init__(self, api_key):
        self.voices = SimpleNamespace(ivc=_IVC())


@unittest.skipIf(api is None, "Project dependencies are not installed on the host.")
class AdminVoiceWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = _Database()
        self.auth = patch.object(api, "_require_auth")
        self.get_db = patch.object(api, "get_db", return_value=self.db)
        self.auth.start()
        self.get_db.start()

    async def asyncTearDown(self):
        self.get_db.stop()
        self.auth.stop()

    async def test_add_voice_does_not_require_consent_data(self):
        voice = await api.add_system_voice(
            api.SystemVoiceIn(name="Existing Creator", elevenlabs_voice_id="voice-id"),
        )

        self.assertEqual(voice.name, "Existing Creator")
        self.assertEqual(voice.elevenlabs_voice_id, "voice-id")
        self.assertEqual(
            self.db.system_voices.documents,
            [{
                "_id": "voice-1",
                "name": "Existing Creator",
                "elevenlabs_voice_id": "voice-id",
            }],
        )

    async def test_clone_voice_does_not_require_consent_data(self):
        upload = api.UploadFile(filename="reference.mp3", file=io.BytesIO(b"reference audio"))

        with (
            patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}),
            patch.object(api, "validate_reference_audio"),
            patch.object(api, "ElevenLabs", _ElevenLabs),
        ):
            voice = await api.clone_voice_from_files(
                name="Cloned Creator",
                files=[upload],
            )

        self.assertEqual(voice.elevenlabs_voice_id, "cloned-voice-id")
        self.assertEqual(
            self.db.system_voices.documents[0],
            {
                "_id": "voice-1",
                "name": "Cloned Creator",
                "elevenlabs_voice_id": "cloned-voice-id",
            },
        )


@unittest.skipIf(bot is None, "Project dependencies are not installed on the host.")
class ExistingVoiceGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_with_existing_voice_without_consent_data(self):
        message = SimpleNamespace(
            text="שלום",
            reply_text=AsyncMock(),
            reply_chat_action=AsyncMock(),
            reply_voice=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            message=message,
        )
        active_voice = {
            "id": "legacy-voice",
            "name": "Existing Creator",
            "elevenlabs_voice_id": "voice-id",
        }

        with (
            patch.object(bot, "is_authorized", AsyncMock(return_value=True)),
            patch.object(bot, "get_active_voice_doc", AsyncMock(return_value=active_voice)),
            patch.object(bot, "get_voice_settings", AsyncMock(return_value={})),
            patch.object(bot, "get_user_prompt", AsyncMock(return_value="")),
            patch.object(bot, "get_user_effect", AsyncMock(return_value=None)),
            patch.object(bot, "get_user_settings", AsyncMock(return_value={"speed": 1.0, "language": "he"})),
            patch.object(bot, "text_to_speech", return_value=b"mp3"),
            patch.object(bot, "process_audio_with_effect", return_value=b"ogg"),
            patch.object(bot, "log_run", AsyncMock()),
        ):
            await bot.handle_text(update, SimpleNamespace())

        message.reply_voice.assert_awaited_once_with(voice=b"ogg")
        message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
