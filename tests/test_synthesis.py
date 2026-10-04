import unittest
from unittest.mock import patch

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


class _Client:
    def __init__(self):
        self.text_to_speech = _TextToSpeech()


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


if __name__ == "__main__":
    unittest.main()
