import json
import unittest
from unittest.mock import AsyncMock, patch

try:
    import bot
except ModuleNotFoundError:
    bot = None


class _TextToSpeech:
    def __init__(self):
        self.request = None

    def convert(self, **kwargs):
        self.request = kwargs
        return [b"audio"]


class _SpeechToSpeech:
    def __init__(self):
        self.request = None

    def convert(self, **kwargs):
        self.request = kwargs
        return [b"audio"]


class _Client:
    def __init__(self):
        self.text_to_speech = _TextToSpeech()
        self.speech_to_speech = _SpeechToSpeech()


@unittest.skipIf(bot is None, "Project dependencies are not installed on the host.")
class SynthesisTests(unittest.TestCase):
    def test_v4_tts_applies_creator_settings_and_safe_speed(self):
        client = _Client()
        with patch.object(bot, "_get_elevenlabs", return_value=client):
            audio = bot.text_to_speech(
                "שלום",
                "authorized-voice",
                speed=2.0,
                language="he",
                voice_settings={"stability": 0.5, "similarity_boost": 0.8, "style": 0.0},
            )

        self.assertEqual(audio, b"audio")
        self.assertEqual(client.text_to_speech.request["model_id"], "eleven_v4")
        self.assertEqual(client.text_to_speech.request["language_code"], "he")
        self.assertEqual(client.text_to_speech.request["voice_settings"]["speed"], 1.2)
        self.assertTrue(client.text_to_speech.request["voice_settings"]["use_speaker_boost"])

    def test_sts_serialises_voice_settings_as_json(self):
        """The multipart endpoint rejects a dict, so settings must go as a JSON string."""
        client = _Client()
        with patch.object(bot, "_get_elevenlabs", return_value=client):
            audio = bot.speech_to_speech(
                b"recording",
                "authorized-voice",
                voice_settings={"stability": 0.8, "similarity_boost": 0.95, "style": 0.0},
            )

        self.assertEqual(audio, b"audio")
        sent = client.speech_to_speech.request["voice_settings"]
        self.assertIsInstance(sent, str)
        self.assertEqual(
            json.loads(sent),
            {
                "stability": 0.8,
                "similarity_boost": 0.95,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        )

    def test_sts_uses_a_voice_conversion_capable_model(self):
        """eleven_v4 is text-to-speech only; speech-to-speech needs an sts model."""
        self.assertIn("sts", bot.STS_MODEL)

    def test_sts_isolates_speech_and_keeps_bitrate_high(self):
        client = _Client()
        with patch.object(bot, "_get_elevenlabs", return_value=client):
            bot.speech_to_speech(b"recording", "authorized-voice")

        request = client.speech_to_speech.request
        self.assertTrue(request["remove_background_noise"])
        self.assertEqual(request["output_format"], "mp3_44100_192")

    def test_tts_asks_for_the_highest_mp3_bitrate(self):
        client = _Client()
        with patch.object(bot, "_get_elevenlabs", return_value=client):
            bot.text_to_speech("שלום", "authorized-voice")

        self.assertEqual(client.text_to_speech.request["output_format"], "mp3_44100_192")


@unittest.skipIf(bot is None, "Project dependencies are not installed on the host.")
class RecordingRouteTests(unittest.IsolatedAsyncioTestCase):
    """The converter has no Hebrew, so Hebrew recordings must not go through it."""

    VOICE = {"id": "voice-doc", "elevenlabs_voice_id": "voice-id", "name": "Creator"}

    def setUp(self):
        self.settings_patch = patch.object(
            bot, "get_voice_settings", AsyncMock(return_value={"stability": 0.5}))
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    async def test_hebrew_recording_is_transcribed_and_re_spoken(self):
        with (
            patch.object(bot, "text_to_speech", return_value=b"tts") as tts,
            patch.object(bot, "speech_to_speech") as sts,
        ):
            audio, model = await bot.convert_recording_to_voice(
                1, b"recording", self.VOICE,
                {"language": "he", "speed": 1.0},
                transcription="היי מותק",
            )

        self.assertEqual((audio, model), (b"tts", bot.TTS_MODEL))
        sts.assert_not_called()
        self.assertEqual(tts.call_args.args[0], "היי מותק")

    async def test_supported_language_still_uses_voice_conversion(self):
        with (
            patch.object(bot, "text_to_speech") as tts,
            patch.object(bot, "speech_to_speech", return_value=b"sts") as sts,
            patch.object(bot, "maybe_pitch_match", AsyncMock(return_value=b"recording")),
        ):
            audio, model = await bot.convert_recording_to_voice(
                1, b"recording", self.VOICE,
                {"language": "en", "speed": 1.0},
                transcription="hey sweetie",
            )

        self.assertEqual((audio, model), (b"sts", bot.STS_MODEL))
        tts.assert_not_called()
        sts.assert_called_once()

    async def test_hebrew_falls_back_to_conversion_when_transcription_is_empty(self):
        with (
            patch.object(bot, "transcribe", return_value="  "),
            patch.object(bot, "text_to_speech") as tts,
            patch.object(bot, "speech_to_speech", return_value=b"sts") as sts,
            patch.object(bot, "maybe_pitch_match", AsyncMock(return_value=b"recording")),
        ):
            audio, model = await bot.convert_recording_to_voice(
                1, b"recording", self.VOICE, {"language": "he", "speed": 1.0},
            )

        self.assertEqual((audio, model), (b"sts", bot.STS_MODEL))
        tts.assert_not_called()
        sts.assert_called_once()

    def test_hebrew_is_absent_from_every_converter_language(self):
        self.assertNotIn("he", bot.STS_LANGUAGES)


if __name__ == "__main__":
    unittest.main()
