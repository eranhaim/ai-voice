"""Modal GPU service for the self-hosted voice engines: Applio (RVC) and Seed-VC.

Deploy:   modal deploy gpu_service/voice_lab.py
Smoke:    modal run gpu_service/voice_lab.py::smoke

The bot calls these functions through `engines/gpu.py`. Nothing here talks to
MongoDB or object storage: audio goes in and out as bytes, and trained Applio
models live on the `voice-lab-models` Modal volume under `/models/<voice_id>/`.

Engines:
- Applio 3.7.1 (RVC). Needs a per-voice training run (`applio_train`, ~15-40 min
  on an A10G), then converts any recording into that voice (`ApplioConverter`).
- Seed-VC v1 (zero-shot). No training: converts a recording using a short
  reference clip of the target voice (`seedvc_convert`).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
import time

import modal

APP_NAME = "voice-lab"
app = modal.App(APP_NAME)

APPLIO_HOME = "/opt/applio"
APPLIO_TAG = "3.7.1"
SEEDVC_HOME = "/opt/seedvc"
# seed-vc is archived; pin the last commit so rebuilds stay reproducible.
SEEDVC_REF = "51383efd921027683c89e5348211d93ff12ac2a8"
MODELS_DIR = "/models"
HF_CACHE = "/cache/hf"

SAMPLE_RATE = 40000

models_vol = modal.Volume.from_name("voice-lab-models", create_if_missing=True)
hf_cache_vol = modal.Volume.from_name("voice-lab-hf-cache", create_if_missing=True)

applio_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git", "build-essential", "libsndfile1")
    .run_commands(
        f"git clone --depth 1 --branch {APPLIO_TAG} "
        f"https://github.com/IAHispano/Applio {APPLIO_HOME}",
        f"cd {APPLIO_HOME} && pip install -r requirements-engine.txt",
        # Pretrained HiFi-GAN generators, contentvec embedder and RMVPE.
        f"cd {APPLIO_HOME} && python core.py prerequisites "
        "--pretraineds-hifigan --models --no-exe",
    )
    .env({"APPLIO_CONFIG_DIR": "/tmp/applio-config"})
)

# Seed-VC's pins (old protobuf via descript-audio-codec) break the Modal runtime,
# so it gets its own virtualenv and runs as a subprocess with that interpreter.
SEEDVC_PY = "/opt/seedvc-venv/bin/python"
SEEDVC_DEPS = " ".join([
    "torch==2.4.0", "torchvision==0.19.0", "torchaudio==2.4.0",
    "scipy==1.13.1", "librosa==0.10.2", "'huggingface-hub>=0.28.1,<1.0'",
    "munch==4.0.0", "einops==0.8.0", "descript-audio-codec==1.0.0",
    "pydub==0.25.1", "transformers==4.46.3", "soundfile==0.12.1",
    "numpy==1.26.4", "hydra-core==1.3.2", "pyyaml", "accelerate", "python-dotenv",
])
seedvc_image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg", "git", "libsndfile1")
    .run_commands(
        f"git clone https://github.com/Plachtaa/seed-vc {SEEDVC_HOME}",
        f"cd {SEEDVC_HOME} && git checkout {SEEDVC_REF}",
        "python -m venv /opt/seedvc-venv",
        f"/opt/seedvc-venv/bin/pip install --no-cache-dir {SEEDVC_DEPS}",
    )
    .env({"HF_HOME": HF_CACHE})
)


# ── shared audio helpers (run inside the containers) ─────────────────────────

def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.decode(errors='replace')[:400]}")


def _to_wav(audio: bytes, path: str, sample_rate: int = 44100) -> str:
    src = path + ".src"
    with open(src, "wb") as f:
        f.write(audio)
    _ffmpeg(["-i", src, "-ac", "1", "-ar", str(sample_rate), path])
    os.unlink(src)
    return path


def _wav_to_mp3(path: str) -> bytes:
    out = path + ".mp3"
    _ffmpeg(["-i", path, "-c:a", "libmp3lame", "-b:a", "192k", out])
    with open(out, "rb") as f:
        return f.read()


# ── Applio (RVC) ─────────────────────────────────────────────────────────────

def _voice_dir(voice_id: str) -> str:
    return os.path.join(MODELS_DIR, voice_id)


@app.function(
    image=applio_image,
    gpu="A10G",
    cpu=8,
    memory=32768,
    timeout=4 * 3600,
    volumes={MODELS_DIR: models_vol},
)
def applio_train(
    voice_id: str,
    samples: list[bytes],
    total_epoch: int = 250,
    batch_size: int = 8,
) -> dict:
    """Train an Applio RVC model for `voice_id` from raw sample audio.

    Writes `/models/<voice_id>/model.pth` and `model.index` on success, or
    `failed.json` on failure. Status is read back by `applio_status`.
    """
    vdir = _voice_dir(voice_id)
    os.makedirs(vdir, exist_ok=True)
    for stale in ("model.pth", "model.index", "failed.json"):
        try:
            os.unlink(os.path.join(vdir, stale))
        except FileNotFoundError:
            pass
    with open(os.path.join(vdir, "training.json"), "w") as f:
        json.dump({"started_at": time.time(), "epochs": total_epoch, "samples": len(samples)}, f)
    models_vol.commit()

    logs_dir = tempfile.mkdtemp(prefix="applio_logs_")
    os.environ["APPLIO_LOGS_DIR"] = logs_dir
    os.chdir(APPLIO_HOME)
    import sys
    if APPLIO_HOME not in sys.path:
        sys.path.insert(0, APPLIO_HOME)

    model_name = f"v{voice_id}"
    exp_dir = os.path.join(logs_dir, model_name)
    try:
        dataset = tempfile.mkdtemp(prefix="dataset_")
        for i, data in enumerate(samples):
            _to_wav(data, os.path.join(dataset, f"sample_{i:03d}.wav"))

        import core  # Applio's core.py; reads APPLIO_LOGS_DIR at import time.

        cores = os.cpu_count() or 4
        print(core.run_preprocess_script(
            model_name=model_name,
            dataset_path=dataset,
            sample_rate=SAMPLE_RATE,
            cpu_cores=cores,
            cut_preprocess="Automatic",
            process_effects=False,
            chunk_len=3.0,
            overlap_len=0.3,
            normalization_mode="none",
        ), flush=True)
        if not glob.glob(os.path.join(exp_dir, "sliced_audios", "*.wav")):
            raise RuntimeError("preprocess produced no audio slices")

        print(core.run_extract_script(
            model_name=model_name,
            f0_method="rmvpe",
            cpu_cores=cores,
            gpu=0,
            sample_rate=SAMPLE_RATE,
            embedder_model="contentvec",
            embedder_model_custom=None,
            include_mutes=2,
        ), flush=True)

        save_every = max(10, total_epoch // 5)
        print(core.run_train_script(
            model_name=model_name,
            save_every_epoch=save_every,
            save_only_latest=True,
            save_every_weights=True,
            total_epoch=total_epoch,
            sample_rate=SAMPLE_RATE,
            batch_size=batch_size,
            gpu=0,
            pretrained=True,
            cleanup=False,
            index_algorithm="Auto",
            cache_data_in_gpu=False,
            custom_pretrained=False,
            vocoder="HiFi-GAN",
            checkpointing=False,
        ), flush=True)

        weights = [
            p for p in glob.glob(os.path.join(exp_dir, f"{model_name}_*e_*s.pth"))
        ]
        if not weights:
            raise RuntimeError("training finished without exporting weights")

        def _epoch(p: str) -> int:
            try:
                return int(os.path.basename(p).rsplit("_", 2)[-2].rstrip("e"))
            except ValueError:
                return -1

        best = max(weights, key=_epoch)
        index = os.path.join(exp_dir, f"{model_name}.index")
        if not os.path.exists(index):
            found = glob.glob(os.path.join(exp_dir, "*.index"))
            if not found:
                raise RuntimeError("training finished without an index file")
            index = found[0]

        shutil.copy(best, os.path.join(vdir, "model.pth"))
        shutil.copy(index, os.path.join(vdir, "model.index"))
        result = {"status": "ready", "weights": os.path.basename(best), "epoch": _epoch(best)}
    except Exception as exc:  # recorded for the bot, then re-raised for Modal logs
        result = {"status": "failed", "error": str(exc)[:1000]}
        with open(os.path.join(vdir, "failed.json"), "w") as f:
            json.dump(result, f)
        models_vol.commit()
        raise
    finally:
        shutil.rmtree(logs_dir, ignore_errors=True)

    models_vol.commit()
    return result


@app.function(image=modal.Image.debian_slim(), volumes={MODELS_DIR: models_vol}, timeout=60)
def applio_status(voice_id: str) -> dict:
    """`ready`, `failed` (with error), `training`, or `missing`."""
    models_vol.reload()
    vdir = _voice_dir(voice_id)
    if os.path.exists(os.path.join(vdir, "model.pth")) and os.path.exists(
        os.path.join(vdir, "model.index")
    ):
        return {"status": "ready"}
    failed = os.path.join(vdir, "failed.json")
    if os.path.exists(failed):
        with open(failed) as f:
            return json.load(f)
    if os.path.exists(os.path.join(vdir, "training.json")):
        return {"status": "training"}
    return {"status": "missing"}


@app.function(image=modal.Image.debian_slim(), volumes={MODELS_DIR: models_vol}, timeout=60)
def applio_delete(voice_id: str) -> None:
    shutil.rmtree(_voice_dir(voice_id), ignore_errors=True)
    models_vol.commit()


@app.cls(
    image=applio_image,
    gpu="A10G",
    timeout=600,
    scaledown_window=300,
    volumes={MODELS_DIR: models_vol},
)
class ApplioConverter:
    @modal.enter()
    def load(self) -> None:
        import sys
        os.environ["APPLIO_LOGS_DIR"] = "/tmp/applio-logs"
        os.chdir(APPLIO_HOME)
        if APPLIO_HOME not in sys.path:
            sys.path.insert(0, APPLIO_HOME)
        import core
        self.core = core

    @modal.method()
    def convert(
        self,
        voice_id: str,
        audio: bytes,
        pitch: int = 0,
        index_rate: float = 0.75,
        protect: float = 0.33,
        volume_envelope: float = 1.0,
        f0_method: str = "rmvpe",
    ) -> bytes:
        """Convert `audio` into the trained voice. Returns MP3 bytes."""
        models_vol.reload()
        vdir = _voice_dir(voice_id)
        pth = os.path.join(vdir, "model.pth")
        index = os.path.join(vdir, "model.index")
        if not os.path.exists(pth):
            raise RuntimeError(f"no trained Applio model for voice {voice_id}")

        work = tempfile.mkdtemp(prefix="applio_inf_")
        try:
            src = _to_wav(audio, os.path.join(work, "input.wav"))
            out = os.path.join(work, "output.wav")
            self.core.run_infer_script(
                pitch=int(pitch),
                index_rate=float(index_rate),
                volume_envelope=float(volume_envelope),
                protect=float(protect),
                f0_method=f0_method,
                input_path=src,
                output_path=out,
                pth_path=pth,
                index_path=index if os.path.exists(index) else "",
                f0_autotune=False,
                f0_autotune_strength=1.0,
                proposed_pitch=False,
                proposed_pitch_threshold=155.0,
                export_format="WAV",
                embedder_model="contentvec",
            )
            if not os.path.exists(out) or os.path.getsize(out) == 0:
                raise RuntimeError("Applio produced no output audio")
            return _wav_to_mp3(out)
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ── Seed-VC (zero-shot) ──────────────────────────────────────────────────────

@app.function(
    image=seedvc_image,
    gpu="A10G",
    timeout=600,
    scaledown_window=300,
    volumes={HF_CACHE: hf_cache_vol},
)
def seedvc_convert(
    source: bytes,
    reference: bytes,
    diffusion_steps: int = 30,
    inference_cfg_rate: float = 0.7,
    semitone_shift: int = 0,
    length_adjust: float = 1.0,
) -> bytes:
    """Convert `source` speech into the voice of `reference`. Returns MP3 bytes.

    Uses the 22 kHz speech model (f0-condition off). Seed-VC only looks at the
    first ~25 s of the reference, so a short clean clip is ideal.
    """
    work = tempfile.mkdtemp(prefix="seedvc_")
    try:
        src = _to_wav(source, os.path.join(work, "source.wav"), 22050)
        ref = _to_wav(reference, os.path.join(work, "reference.wav"), 22050)
        out_dir = os.path.join(work, "out")
        cmd = [
            SEEDVC_PY, "inference.py",
            "--source", src,
            "--target", ref,
            "--output", out_dir,
            "--diffusion-steps", str(int(diffusion_steps)),
            "--length-adjust", str(float(length_adjust)),
            "--inference-cfg-rate", str(float(inference_cfg_rate)),
            "--f0-condition", "False",
            "--auto-f0-adjust", "False",
            "--semi-tone-shift", str(int(semitone_shift)),
            "--fp16", "True",
        ]
        r = subprocess.run(cmd, cwd=SEEDVC_HOME, capture_output=True, text=True)
        hf_cache_vol.commit()
        if r.returncode != 0:
            raise RuntimeError(f"seed-vc failed: {(r.stderr or r.stdout)[-1500:]}")
        outputs = glob.glob(os.path.join(out_dir, "*.wav"))
        if not outputs:
            raise RuntimeError(f"seed-vc produced no output: {r.stdout[-800:]}")
        return _wav_to_mp3(outputs[0])
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.local_entrypoint()
def smoke(source_path: str = "", reference_path: str = "") -> None:
    """Quick check: `modal run gpu_service/voice_lab.py::smoke --source-path a.mp3 --reference-path b.mp3`."""
    if not (source_path and reference_path):
        print("voice-lab deployed; pass --source-path and --reference-path to test Seed-VC")
        return
    with open(source_path, "rb") as f:
        src = f.read()
    with open(reference_path, "rb") as f:
        ref = f.read()
    out = seedvc_convert.remote(src, ref)
    with open("seedvc_smoke.mp3", "wb") as f:
        f.write(out)
    print(f"wrote seedvc_smoke.mp3 ({len(out)} bytes)")
