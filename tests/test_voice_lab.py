"""Offline tests for voice_lab: engine routing, fallbacks and comparison.

Run: python -m unittest tests.test_voice_lab
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_lab  # noqa: E402
from engines import STS_ENGINES, TTS_ENGINES  # noqa: E402


def run(coro):
    return asyncio.run(coro)


VOICE = {
    "id": "64b000000000000000000001",
    "name": "Test",
    "elevenlabs_voice_id": "el-voice",
    "sample_urls": [],
    "minimax_voice_id": "voice64b000000000000000000001",
    "reference_audio_url": "s3://bucket/voices/reference/x.mp3",
    "applio_status": "ready",
}


class _Patched(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(voice_lab, "set_voice_fields", mock.AsyncMock()),
            mock.patch.object(voice_lab.s3, "download_sample", return_value=b"REF"),
            mock.patch.object(voice_lab.s3, "upload_object", side_effect=lambda k, d, *a: f"s3://bucket/{k}"),
            mock.patch.object(voice_lab.s3, "presigned_url", side_effect=lambda u, e=3600: "https://signed/" + u),
            mock.patch.object(voice_lab.minimax, "is_enabled", return_value=True),
            mock.patch.object(voice_lab.modelslab, "is_enabled", return_value=True),
            mock.patch.object(voice_lab.gpu, "is_enabled", return_value=True),
            mock.patch.object(voice_lab.audio, "to_mp3", side_effect=lambda a, *x, **k: a),
            mock.patch.object(voice_lab.audio, "reference_clip", side_effect=lambda s, *x: b"".join(s)),
            mock.patch.object(voice_lab.pitch, "estimate_f0_hz", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def voice(self, **overrides):
        return {**VOICE, **overrides}


class PlainTextTest(unittest.TestCase):
    def test_strips_nikud_and_audio_tags(self):
        self.assertEqual(voice_lab.plain_text("[giggles] שָׁלוֹם  לְךָ [sighs]"), "שלום לך")


class TtsRoutingTest(_Patched):
    def test_elevenlabs_uses_baseline_callable(self):
        out = run(voice_lab.synthesize("elevenlabs", "x", self.voice(), {}, lambda: b"EL", None))
        self.assertEqual(out, b"EL")

    def test_minimax_gets_plain_text_and_existing_clone(self):
        with mock.patch.object(voice_lab.minimax, "synthesize", return_value=b"MM") as synth:
            out = run(voice_lab.synthesize(
                "minimax", "[giggles] הַיי", self.voice(), {"speed": 1.1, "language": "he"}, None, None,
            ))
        self.assertEqual(out, b"MM")
        synth.assert_called_once_with("היי", VOICE["minimax_voice_id"], 1.1, "he")

    def test_minimax_reclones_expired_voice_once(self):
        calls = []

        def synth(text, vid, *a):
            calls.append(vid)
            if len(calls) == 1:
                raise RuntimeError("MiniMax TTS failed: voice id not exist")
            return b"MM2"

        with mock.patch.object(voice_lab.minimax, "synthesize", side_effect=synth), \
             mock.patch.object(voice_lab.minimax, "clone", return_value="voice-new") as clone:
            out = run(voice_lab.synthesize("minimax", "hi", self.voice(), {}, None, None))
        self.assertEqual(out, b"MM2")
        self.assertEqual(calls, [VOICE["minimax_voice_id"], "voice-new"])
        clone.assert_called_once()

    def test_minimax_then_applio_chains_conversion(self):
        with mock.patch.object(voice_lab.minimax, "synthesize", return_value=b"MM"), \
             mock.patch.object(voice_lab.gpu, "applio_convert", return_value=b"RVC") as conv:
            out = run(voice_lab.synthesize("minimax+applio", "hi", self.voice(), {}, None, None))
        self.assertEqual(out, b"RVC")
        conv.assert_called_once_with(VOICE["id"], b"MM")

    def test_modelslab_tts_uses_signed_reference(self):
        with mock.patch.object(voice_lab.modelslab, "text_to_speech", return_value=b"ML") as tts:
            out = run(voice_lab.synthesize("modelslab", "שלום", self.voice(), {"language": "he"}, None, None))
        self.assertEqual(out, b"ML")
        tts.assert_called_once_with("שלום", "https://signed/" + VOICE["reference_audio_url"], "he")

    def test_missing_minimax_key_is_unavailable(self):
        with mock.patch.object(voice_lab.minimax, "is_enabled", return_value=False):
            with self.assertRaises(voice_lab.EngineUnavailable):
                run(voice_lab.synthesize("minimax", "hi", self.voice(minimax_voice_id=None), {}, None, None))


class StsRoutingTest(_Patched):
    def test_applio_needs_trained_model(self):
        for status in ("training", "failed", None):
            with self.assertRaises(voice_lab.EngineUnavailable):
                run(voice_lab.convert("applio", b"rec", self.voice(applio_status=status), None, None))

    def test_male_speaker_is_shifted_up_for_female_voice(self):
        hz = {b"rec": 110.0, b"REF": 220.0}
        with mock.patch.object(voice_lab.pitch, "estimate_f0_hz", side_effect=lambda a: hz.get(a)), \
             mock.patch.object(voice_lab.pitch, "pitch_shift_ogg", return_value=b"rec+12") as shift, \
             mock.patch.object(voice_lab.gpu, "applio_convert", return_value=b"RVC") as applio, \
             mock.patch.object(voice_lab.gpu, "seedvc_convert", return_value=b"SVC") as seedvc:
            run(voice_lab.convert("applio", b"rec", self.voice(), None, None))
            run(voice_lab.convert("seedvc", b"rec", self.voice(), None, None))
        applio.assert_called_once_with(VOICE["id"], b"rec", 12)
        shift.assert_called_once_with(b"rec", 12)
        seedvc.assert_called_once_with(b"rec+12", b"REF")

    def test_applio_not_for_system_voices(self):
        with self.assertRaises(voice_lab.EngineUnavailable):
            run(voice_lab.convert("applio", b"rec", self.voice(is_system=True), None, None))

    def test_seedvc_uses_stored_reference(self):
        with mock.patch.object(voice_lab.gpu, "seedvc_convert", return_value=b"SVC") as conv:
            out = run(voice_lab.convert("seedvc", b"rec", self.voice(), None, None))
        self.assertEqual(out, b"SVC")
        conv.assert_called_once_with(b"rec", b"REF")

    def test_seedvc_falls_back_to_elevenlabs_preview(self):
        v = self.voice(reference_audio_url=None, is_system=True)
        with mock.patch.object(voice_lab.gpu, "seedvc_convert", return_value=b"SVC") as conv:
            run(voice_lab.convert("seedvc", b"rec", v, None, lambda vid: b"PREVIEW"))
        conv.assert_called_once_with(b"rec", b"PREVIEW")

    def test_modelslab_uploads_source_and_signs_both(self):
        with mock.patch.object(voice_lab.modelslab, "voice_to_voice", return_value=b"ML") as v2v:
            out = run(voice_lab.convert("modelslab", b"rec", self.voice(), None, None))
        self.assertEqual(out, b"ML")
        src, ref = v2v.call_args.args
        self.assertTrue(src.startswith("https://signed/s3://bucket/runs/modelslab/"))
        self.assertEqual(ref, "https://signed/" + VOICE["reference_audio_url"])


class CompareTest(_Patched):
    def test_compare_sts_reports_every_engine_without_raising(self):
        with mock.patch.object(voice_lab.gpu, "applio_convert", side_effect=RuntimeError("boom")), \
             mock.patch.object(voice_lab.gpu, "seedvc_convert", return_value=b"SVC"), \
             mock.patch.object(voice_lab.modelslab, "voice_to_voice", return_value=b"ML"):
            results = run(voice_lab.compare_sts(b"rec", self.voice(), lambda: b"EL", None))
        self.assertEqual([r.engine for r in results], list(STS_ENGINES))
        by = {r.engine: r for r in results}
        self.assertEqual(by["elevenlabs"].audio, b"EL")
        self.assertIsNone(by["applio"].audio)
        self.assertIn("boom", by["applio"].error)
        self.assertEqual(by["seedvc"].audio, b"SVC")

    def test_compare_tts_covers_all_engines(self):
        with mock.patch.object(voice_lab.minimax, "synthesize", return_value=b"MM"), \
             mock.patch.object(voice_lab.modelslab, "text_to_speech", return_value=b"ML"), \
             mock.patch.object(voice_lab.gpu, "applio_convert", return_value=b"RVC"), \
             mock.patch.object(voice_lab.gpu, "seedvc_convert", return_value=b"SVC"):
            results = run(voice_lab.compare_tts("hi", self.voice(), {}, lambda: b"EL", None))
        self.assertEqual([r.engine for r in results], list(TTS_ENGINES))
        self.assertTrue(all(r.audio for r in results), [r.error for r in results])


class PrepareTest(_Patched):
    def test_prepare_reports_each_step(self):
        with mock.patch.object(voice_lab.minimax, "clone", return_value="voiceX"), \
             mock.patch.object(voice_lab.gpu, "spawn_applio_training", return_value="fc-1"):
            report = run(voice_lab.prepare_voice(VOICE["id"], [b"a", b"b"]))
        self.assertEqual(report, {"reference": "ok", "minimax": "ok", "applio": "ok"})

    def test_prepare_skips_unconfigured_engines(self):
        with mock.patch.object(voice_lab.minimax, "is_enabled", return_value=False), \
             mock.patch.object(voice_lab.gpu, "is_enabled", return_value=False):
            report = run(voice_lab.prepare_voice(VOICE["id"], [b"a"]))
        self.assertTrue(report["minimax"].startswith("skipped"))
        self.assertTrue(report["applio"].startswith("skipped"))


if __name__ == "__main__":
    unittest.main()
