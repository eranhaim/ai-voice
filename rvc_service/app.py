"""Modal app for RVC training and inference.

Deploy with:
    modal deploy rvc_service/app.py

The bot calls these functions via modal.Function.lookup("rvc-voice", "...").
"""
import modal

app = modal.App("rvc-voice")

RVC_REPO = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
RVC_HOME = "/opt/rvc"
HF_BASE = "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main"

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install(
        "ffmpeg",
        "git",
        "wget",
        "build-essential",
        "libsndfile1",
    )
    .pip_install(
        "torch==2.1.0",
        "torchaudio==2.1.0",
        "torchvision==0.16.0",
        "numpy<2.0",
        "scipy",
        "soundfile",
        "librosa==0.9.1",
        "faiss-cpu==1.7.3",
        "praat-parselmouth>=0.4.2",
        "pyworld==0.3.2",
        "fairseq==0.12.2",
        "boto3",
        "pymongo",
        "tqdm",
        "Cython",
        "matplotlib",
        "tensorboardX",
        "pydub",
        "ffmpeg-python",
        "torchcrepe",
    )
    .run_commands(
        f"git clone {RVC_REPO} {RVC_HOME}",
        # Pretrained models and feature extractors
        f"mkdir -p {RVC_HOME}/assets/hubert {RVC_HOME}/assets/pretrained_v2 {RVC_HOME}/assets/rmvpe",
        f"wget -q -O {RVC_HOME}/assets/hubert/hubert_base.pt {HF_BASE}/hubert_base.pt",
        f"wget -q -O {RVC_HOME}/assets/rmvpe/rmvpe.pt {HF_BASE}/rmvpe.pt",
        f"wget -q -O {RVC_HOME}/assets/pretrained_v2/D40k.pth {HF_BASE}/pretrained_v2/D40k.pth",
        f"wget -q -O {RVC_HOME}/assets/pretrained_v2/G40k.pth {HF_BASE}/pretrained_v2/G40k.pth",
        f"wget -q -O {RVC_HOME}/assets/pretrained_v2/f0D40k.pth {HF_BASE}/pretrained_v2/f0D40k.pth",
        f"wget -q -O {RVC_HOME}/assets/pretrained_v2/f0G40k.pth {HF_BASE}/pretrained_v2/f0G40k.pth",
    )
    .add_local_python_source("rvc_service")
)

vol = modal.Volume.from_name("rvc-models", create_if_missing=True)

SECRET = modal.Secret.from_name("ai-voice")


@app.function(
    gpu="A10G",
    image=image,
    volumes={"/models": vol},
    timeout=4 * 3600,
    secrets=[SECRET],
)
def train_voice(voice_id: str, sample_urls: list[str], total_epoch: int = 150) -> dict:
    """Train an RVC model for the given voice. Long-running (15-60 min).

    Updates MongoDB voices doc with training_status=ready|failed and stores
    the model + index in S3 + persistent Modal volume.
    """
    from rvc_service.training import run_training
    return run_training(voice_id, sample_urls, total_epoch)


@app.function(
    gpu="A10G",
    image=image,
    volumes={"/models": vol},
    timeout=300,
    secrets=[SECRET],
    min_containers=1,
)
def convert(voice_id: str, audio_bytes: bytes, f0_up_key: int = 0) -> bytes:
    """Convert input audio to the target RVC voice. Returns mp3 bytes."""
    from rvc_service.inference import run_inference
    return run_inference(voice_id, audio_bytes, f0_up_key)


@app.local_entrypoint()
def smoke():
    """Local entrypoint for smoke testing once deployed."""
    print("rvc-voice app deployed. Use Modal CLI to invoke functions.")
