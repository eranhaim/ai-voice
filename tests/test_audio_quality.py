import unittest
from unittest.mock import patch

from audio_quality import ReferenceAudioError, validate_reference_audio


def _metadata(duration: float) -> dict:
    return {
        "format": {"duration": str(duration)},
        "streams": [{"codec_type": "audio", "codec_name": "opus"}],
    }


class ReferenceAudioValidationTests(unittest.TestCase):
    def test_rejects_empty_audio(self):
        with self.assertRaises(ReferenceAudioError):
            validate_reference_audio(b"")

    @patch("audio_quality._decode_mono_pcm")
    @patch("audio_quality._probe_audio")
    def test_accepts_audible_clean_audio(self, probe, decode):
        probe.return_value = _metadata(15)
        decode.return_value = (500).to_bytes(2, "little", signed=True) * 160

        info = validate_reference_audio(b"authorized fixture")

        self.assertEqual(info.codec, "opus")
        self.assertEqual(info.duration_seconds, 15)
        self.assertGreater(info.rms, 100)

    @patch("audio_quality._decode_mono_pcm")
    @patch("audio_quality._probe_audio")
    def test_rejects_heavily_clipped_audio(self, probe, decode):
        probe.return_value = _metadata(15)
        decode.return_value = (32767).to_bytes(2, "little", signed=True) * 160

        with self.assertRaisesRegex(ReferenceAudioError, "heavily clipped"):
            validate_reference_audio(b"authorized fixture")

    @patch("audio_quality._decode_mono_pcm")
    @patch("audio_quality._probe_audio")
    def test_rejects_short_audio(self, probe, decode):
        probe.return_value = _metadata(4)

        with self.assertRaisesRegex(ReferenceAudioError, "5 seconds"):
            validate_reference_audio(b"authorized fixture")

        decode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
