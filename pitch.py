"""Source-to-target pitch matching for STS.

Voice-to-voice conversion sounds way more natural when the *source* audio is
pre-shifted to roughly match the *target* voice's average pitch -- otherwise
the model preserves the source's prosody and you get e.g. a female voice
delivering content in a clearly male register.

This module does two things:
    1. `estimate_f0_hz(audio_bytes)` -- robust pitch detection on speech
       using librosa's PYIN. We median over voiced frames so octave errors
       and unvoiced segments don't bias the result.
    2. `pitch_shift_ogg(audio_bytes, semitones)` -- pitch-shift any audio
       using ffmpeg's `asetrate -> aresample -> atempo` chain. Output is
       OGG/Opus, ready to feed back into the ElevenLabs STS endpoint.

librosa is heavy (numpy + scipy + numba), so we lazy-import it on first call
to keep bot cold-start fast.
"""

from __future__ import annotations

import io
import logging
import math
import subprocess

logger = logging.getLogger(__name__)

# Below this absolute delta we don't bother shifting -- two voices within ~2
# semitones (about 12% pitch difference) are close enough that the artifacts
# from pitch shifting outweigh the benefit.
MIN_SHIFT_SEMITONES = 2.0

# Cap shifts at one octave. Anything larger is almost certainly an octave
# error in F0 detection (e.g. picking up the first harmonic), and large
# shifts sound bad anyway.
MAX_SHIFT_SEMITONES = 12.0

# Sample rate at which we run pyin. 22.05 kHz is the librosa default and is
# plenty for voice F0 (which sits at 65-600 Hz).
_F0_SAMPLE_RATE = 22050

# Human-voice F0 search range. Bass speakers can dip to ~65 Hz; soprano /
# higher female head voice rarely exceeds 600 Hz. Going wider invites more
# octave errors.
_F0_MIN_HZ = 65.0
_F0_MAX_HZ = 600.0


def _ffmpeg_decode_to_wav_mono(audio_bytes: bytes, sample_rate: int) -> bytes:
    """Decode arbitrary audio to mono PCM s16le WAV at the given sample rate."""
    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "wav", "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg decode failed: {result.stderr.decode(errors='replace')[:200]}"
        )
    return result.stdout


def estimate_f0_hz(audio_bytes: bytes) -> float | None:
    """Estimate the median voiced F0 (Hz) of a speech clip.

    Returns None if too little voiced content is detected (very short audio,
    pure noise, music, etc.). Caller should treat None as "don't shift".
    """
    try:
        import numpy as np
        import soundfile as sf
        import librosa
    except ImportError:
        logger.exception("librosa/soundfile/numpy not installed; pitch detection disabled")
        return None

    try:
        wav = _ffmpeg_decode_to_wav_mono(audio_bytes, _F0_SAMPLE_RATE)
    except Exception:
        logger.exception("F0 decode failed")
        return None

    try:
        y, sr = sf.read(io.BytesIO(wav), dtype="float32")
    except Exception:
        logger.exception("F0 wav read failed")
        return None
    if y.size == 0:
        return None

    try:
        f0, _voiced_flag, _voiced_prob = librosa.pyin(
            y,
            fmin=_F0_MIN_HZ,
            fmax=_F0_MAX_HZ,
            sr=sr,
            frame_length=2048,
            hop_length=512,
        )
    except Exception:
        logger.exception("pyin failed")
        return None

    f0 = np.asarray(f0, dtype=float)
    mask = np.isfinite(f0) & (f0 > 0)
    voiced = int(mask.sum())
    if voiced < 5:
        return None
    # Median is octave-error-resistant; mean would be biased by stray harmonics.
    median_hz = float(np.median(f0[mask]))
    logger.info("F0 estimate: %.1f Hz from %d voiced frames", median_hz, voiced)
    return median_hz


def compute_shift_semitones(
    source_hz: float | None,
    target_hz: float | None,
    min_shift: float = MIN_SHIFT_SEMITONES,
    max_shift: float = MAX_SHIFT_SEMITONES,
) -> float:
    """Returns the shift in semitones, or 0.0 if below noise threshold / invalid."""
    if not source_hz or not target_hz or source_hz <= 0 or target_hz <= 0:
        return 0.0
    delta = 12.0 * math.log2(target_hz / source_hz)
    if abs(delta) < min_shift:
        return 0.0
    return max(-max_shift, min(max_shift, delta))


def _build_atempo_chain(inverse_factor: float) -> str:
    """ffmpeg's atempo filter accepts 0.5-2.0. For more extreme tempo
    adjustments we chain multiple atempo filters together."""
    parts: list[str] = []
    remaining = inverse_factor
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def pitch_shift_ogg(
    audio_bytes: bytes,
    semitones: float,
    sample_rate: int = 48000,
) -> bytes:
    """Pitch-shift audio by N semitones using ffmpeg.

    Trick: `asetrate` shifts both pitch *and* tempo proportionally (like
    speeding up a record); `aresample` re-encodes back to the target rate;
    `atempo` compensates the tempo back. Result: pitch-only shift.

    Returns OGG/Opus bytes. Falls back to the original audio if ffmpeg fails.
    """
    if abs(semitones) < 1e-3:
        return audio_bytes

    factor = 2.0 ** (semitones / 12.0)
    new_rate = max(1, int(round(sample_rate * factor)))
    tempo_chain = _build_atempo_chain(1.0 / factor)

    filter_complex = (
        f"asetrate={new_rate},"
        f"aresample={sample_rate},"
        f"{tempo_chain}"
    )

    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-af", filter_complex,
            "-c:a", "libopus", "-b:a", "64k",
            "-f", "ogg", "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        logger.error(
            "ffmpeg pitch shift failed: %s",
            result.stderr.decode(errors="replace")[:200],
        )
        return audio_bytes
    return result.stdout


def adjust_mp3_speed(mp3_bytes: bytes, speed: float) -> bytes:
    """Adjust playback speed of MP3 audio. speed=1.0 leaves audio unchanged."""
    if abs(speed - 1.0) < 1e-3:
        return mp3_bytes

    tempo_chain = _build_atempo_chain(speed)
    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-af", tempo_chain,
            "-c:a", "libmp3lame", "-q:a", "2",
            "-f", "mp3", "pipe:1",
        ],
        input=mp3_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        logger.error(
            "ffmpeg speed adjust failed: %s",
            result.stderr.decode(errors="replace")[:200],
        )
        return mp3_bytes
    return result.stdout
