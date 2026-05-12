"""RVC inference. Runs inside the Modal container.

Loads a trained voice model (cached on persistent volume) and converts the
input audio to the target voice. Returns mp3 bytes.

This module is imported only inside the Modal container.
"""
import os
import subprocess
import sys
import tempfile

RVC_HOME = "/opt/rvc"
MODELS_VOLUME = "/models"

# Loaded-once-per-container cache of (voice_id -> VC instance)
_VC_CACHE: dict = {}


def _log(msg: str) -> None:
    print(f"[rvc-infer] {msg}", flush=True)


def _s3_client():
    import boto3
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def _ensure_model(voice_id: str) -> tuple[str, str]:
    """Make sure the model + index are present on the local volume. Downloads from S3 if missing."""
    from pymongo import MongoClient
    from bson import ObjectId

    vol_dir = os.path.join(MODELS_VOLUME, voice_id)
    os.makedirs(vol_dir, exist_ok=True)
    model_path = os.path.join(vol_dir, "model.pth")
    index_path = os.path.join(vol_dir, "model.index")
    if os.path.exists(model_path) and os.path.exists(index_path):
        return model_path, index_path

    mongo = MongoClient(os.environ["MONGO_URI"])[os.environ.get("MONGO_DB", "voice_bot")]
    doc = mongo.voices.find_one({"_id": ObjectId(voice_id)})
    if not doc:
        raise RuntimeError(f"voice {voice_id} not found in DB")
    model_url = doc.get("rvc_model_url")
    index_url = doc.get("rvc_index_url")
    if not model_url or not index_url:
        raise RuntimeError(f"voice {voice_id} not fully trained")

    s3 = _s3_client()
    for url, local in [(model_url, model_path), (index_url, index_path)]:
        parts = url.replace("s3://", "").split("/", 1)
        _log(f"downloading {url} -> {local}")
        s3.download_file(parts[0], parts[1], local)

    import modal
    modal.Volume.from_name("rvc-models").commit()
    return model_path, index_path


def _get_vc(voice_id: str, model_path: str):
    """Lazy-load the RVC VC class for a voice. Caches per container."""
    if voice_id in _VC_CACHE:
        return _VC_CACHE[voice_id]

    if RVC_HOME not in sys.path:
        sys.path.insert(0, RVC_HOME)
    os.chdir(RVC_HOME)

    # VC.get_vc reads these env vars to resolve weight + index files.
    weights_dir = os.path.join(RVC_HOME, "assets/weights")
    indexes_dir = os.path.join(RVC_HOME, "assets/indices")
    os.makedirs(weights_dir, exist_ok=True)
    os.makedirs(indexes_dir, exist_ok=True)
    os.environ["weight_root"] = weights_dir
    os.environ["index_root"] = indexes_dir
    os.environ.setdefault("rmvpe_root", os.path.join(RVC_HOME, "assets/rmvpe"))

    from infer.modules.vc.modules import VC
    from configs.config import Config

    config = Config()
    config.device = "cuda:0"
    config.is_half = True

    vc = VC(config)
    weight_name = f"{voice_id}.pth"
    weight_target = os.path.join(weights_dir, weight_name)
    if (
        not os.path.exists(weight_target)
        or os.path.getmtime(weight_target) < os.path.getmtime(model_path)
    ):
        import shutil
        shutil.copy(model_path, weight_target)
    vc.get_vc(weight_name)

    _VC_CACHE[voice_id] = vc
    return vc


def run_inference(
    voice_id: str,
    audio_bytes: bytes,
    f0_up_key: int = 0,
    index_rate: float = 0.95,
    rms_mix_rate: float = 0.05,
    protect: float = 0.33,
    filter_radius: int = 3,
) -> bytes:
    model_path, index_path = _ensure_model(voice_id)

    workdir = tempfile.mkdtemp(prefix="rvc_inf_")
    try:
        in_path = os.path.join(workdir, "input.raw")
        wav_path = os.path.join(workdir, "input.wav")
        out_wav = os.path.join(workdir, "out.wav")
        out_mp3 = os.path.join(workdir, "out.mp3")
        with open(in_path, "wb") as f:
            f.write(audio_bytes)
        # Normalize to 16kHz mono wav so RVC can pick up reliably.
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", in_path,
                "-ar", "16000", "-ac", "1",
                wav_path,
            ],
            check=True,
        )

        vc = _get_vc(voice_id, model_path)
        _log(f"converting voice_id={voice_id} f0_up_key={f0_up_key}")
        try:
            info, audio_pair = vc.vc_single(
                0,                  # sid (speaker id, only one)
                wav_path,           # input
                f0_up_key,
                None,               # f0_file
                "rmvpe",            # f0_method
                index_path,         # file_index
                "",                 # file_index2 (deprecated)
                index_rate,
                filter_radius,
                0,                  # resample_sr (0 keeps native)
                rms_mix_rate,
                protect,
            )
        except Exception:
            _log("vc_single failed")
            raise

        if not audio_pair or audio_pair[0] is None or audio_pair[1] is None:
            raise RuntimeError(f"RVC vc_single returned no audio: {info}")
        tgt_sr, audio_out = audio_pair

        import soundfile as sf
        sf.write(out_wav, audio_out, tgt_sr)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", out_wav,
                "-c:a", "libmp3lame", "-b:a", "128k",
                out_mp3,
            ],
            check=True,
        )
        with open(out_mp3, "rb") as f:
            return f.read()
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
