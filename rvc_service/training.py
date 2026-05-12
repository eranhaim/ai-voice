"""RVC training pipeline. Runs inside the Modal container.

Pipeline:
    1. Download all S3 samples and convert to 40kHz mono wav.
    2. RVC preprocess (slice + normalize).
    3. Extract F0 with rmvpe and HuBERT features.
    4. Train with pretrained v2 weights (f0G/f0D 40k).
    5. Build faiss index.
    6. Upload .pth + .index to S3 and copy to persistent volume.
    7. Update MongoDB doc to status="ready".

This module is imported only inside the Modal container.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

RVC_HOME = "/opt/rvc"
MODELS_VOLUME = "/models"


def _log(msg: str) -> None:
    print(f"[rvc-train] {msg}", flush=True)


def _s3_client():
    import boto3
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def _mongo():
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGO_URI"])
    return client[os.environ.get("MONGO_DB", "voice_bot")]


def _update_status(voice_id: str, status: str, **fields) -> None:
    from bson import ObjectId
    db = _mongo()
    update = {"training_status": status}
    update.update(fields)
    db.voices.update_one({"_id": ObjectId(voice_id)}, {"$set": update})


def _download_samples(sample_urls: list[str], dest_dir: str) -> int:
    s3 = _s3_client()
    count = 0
    for i, url in enumerate(sample_urls):
        try:
            parts = url.replace("s3://", "").split("/", 1)
            bucket, key = parts[0], parts[1]
            raw_path = os.path.join(dest_dir, f"raw_{i}")
            s3.download_file(bucket, key, raw_path)
            wav_path = os.path.join(dest_dir, f"sample_{i:03d}.wav")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", raw_path,
                    "-ar", "40000", "-ac", "1",
                    wav_path,
                ],
                check=True,
            )
            os.remove(raw_path)
            count += 1
        except Exception as e:
            _log(f"sample {i} skipped: {e}")
    return count


def _run(
    cmd: list[str],
    cwd: str = RVC_HOME,
    check: bool = True,
    detect_child_crash: bool = False,
) -> int:
    """Run a subprocess streaming its output to the Modal logs.

    If ``detect_child_crash`` is set, scan for ``Process-N: Traceback`` and
    ``AttributeError/RuntimeError/Exception`` patterns coming from worker
    multiprocessing children whose failures the parent process swallows
    (RVC's train.py is the main offender).
    """
    _log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    last_lines: list[str] = []
    child_crash: str | None = None
    saw_process_traceback = False
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        _log(line)
        last_lines.append(line)
        if len(last_lines) > 80:
            last_lines.pop(0)
        if detect_child_crash:
            if line.startswith("Process ") and "Traceback" in line:
                saw_process_traceback = True
            elif saw_process_traceback and (
                line.startswith("AttributeError")
                or line.startswith("RuntimeError")
                or line.startswith("TypeError")
                or line.startswith("ValueError")
                or line.startswith("KeyError")
                or line.startswith("Exception")
                or line.startswith("OSError")
            ):
                child_crash = line
                saw_process_traceback = False
    proc.wait()
    if check and proc.returncode != 0:
        tail = " | ".join(last_lines[-10:])
        raise RuntimeError(
            f"command failed (rc={proc.returncode}): {' '.join(cmd)} :: {tail}"
        )
    if check and child_crash:
        raise RuntimeError(f"subprocess child crashed: {child_crash}")
    return proc.returncode


def _upload(bucket: str, key: str, local_path: str) -> str:
    _s3_client().upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"


def run_training(voice_id: str, sample_urls: list[str], total_epoch: int = 300) -> dict:
    bucket = os.environ["AWS_S3_BUCKET"]
    exp_name = f"voice_{voice_id}"
    workdir = tempfile.mkdtemp(prefix="rvc_")
    samples_dir = os.path.join(workdir, "samples")
    os.makedirs(samples_dir, exist_ok=True)

    try:
        _update_status(voice_id, "training")

        _log(f"Downloading {len(sample_urls)} samples for {exp_name}")
        n = _download_samples(sample_urls, samples_dir)
        if n == 0:
            raise RuntimeError("no usable samples")
        _log(f"Got {n} samples")

        logs_dir = os.path.join(RVC_HOME, "logs", exp_name)
        if os.path.exists(logs_dir):
            shutil.rmtree(logs_dir)
        os.makedirs(logs_dir, exist_ok=True)

        # 1. Preprocess: slice and normalize. RVC's preprocess takes:
        #    inp_root sr n_p exp_dir no_parallel per
        # Use noparallel=True for deterministic sequential processing inside Modal.
        _run([
            sys.executable, "infer/modules/train/preprocess.py",
            samples_dir, "40000", "2", logs_dir, "True", "3.7",
        ])

        gt_wavs_dir = os.path.join(logs_dir, "0_gt_wavs")
        if not os.path.isdir(gt_wavs_dir) or not os.listdir(gt_wavs_dir):
            raise RuntimeError("preprocess produced no usable slices")

        # 2. Extract f0 with rmvpe.
        _run([
            sys.executable, "infer/modules/train/extract/extract_f0_rmvpe.py",
            "1", "0", "0", logs_dir, "True",
        ])

        # 3. Extract HuBERT features.
        _run([
            sys.executable, "infer/modules/train/extract_feature_print.py",
            "cuda:0", "1", "0", "0", logs_dir, "v2", "True",
        ])

        # 4. Build training filelist.
        _build_filelist(logs_dir, exp_name)

        # 5. Train. Uses pretrained_v2 40k with f0.
        # RVC trainer spawns the actual training as a multiprocessing child
        # and exits the parent process with code 0 even when the child crashed
        # (it relies on the child calling os._exit on success). We detect the
        # crash by scanning subprocess output for Process-N tracebacks.
        save_every = max(total_epoch // 3, 25)
        _run([
            sys.executable, "infer/modules/train/train.py",
            "-e", exp_name,
            "-sr", "40k",
            "-f0", "1",
            "-bs", "8",
            "-g", "0",
            "-te", str(total_epoch),
            "-se", str(save_every),
            "-pg", "assets/pretrained_v2/f0G40k.pth",
            "-pd", "assets/pretrained_v2/f0D40k.pth",
            "-l", "1",
            "-c", "0",
            "-sw", "1",
            "-v", "v2",
        ], detect_child_crash=True)

        # 6. Build faiss index from extracted features.
        _build_index(logs_dir, exp_name)

        # Locate produced artifacts.
        model_path = _find_latest_pth(logs_dir, exp_name)
        index_path = _find_index(logs_dir)
        if not model_path or not index_path:
            raise RuntimeError(f"missing artifacts model={model_path} index={index_path}")

        # 7. Upload to S3 and cache on volume.
        model_key = f"rvc/{voice_id}/{exp_name}.pth"
        index_key = f"rvc/{voice_id}/{exp_name}.index"
        model_url = _upload(bucket, model_key, model_path)
        index_url = _upload(bucket, index_key, index_path)

        vol_dir = os.path.join(MODELS_VOLUME, voice_id)
        os.makedirs(vol_dir, exist_ok=True)
        shutil.copy(model_path, os.path.join(vol_dir, "model.pth"))
        shutil.copy(index_path, os.path.join(vol_dir, "model.index"))

        import modal
        modal.Volume.from_name("rvc-models").commit()

        _update_status(
            voice_id,
            "ready",
            rvc_model_url=model_url,
            rvc_index_url=index_url,
        )
        _log("training complete")
        return {"status": "ready", "model_url": model_url, "index_url": index_url}

    except Exception as exc:
        _log("TRAINING FAILED")
        traceback.print_exc()
        _update_status(voice_id, "failed", training_error=str(exc)[:500])
        return {"status": "failed", "error": str(exc)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _build_filelist(logs_dir: str, exp_name: str) -> None:
    """RVC needs a filelist.txt mapping wavs to f0/feature files."""
    gt_wavs = os.path.join(logs_dir, "0_gt_wavs")
    feature = os.path.join(logs_dir, "3_feature768")
    f0 = os.path.join(logs_dir, "2a_f0")
    f0nsf = os.path.join(logs_dir, "2b-f0nsf")

    names = set()
    for name in os.listdir(gt_wavs):
        if name.endswith(".wav"):
            names.add(name.removesuffix(".wav"))

    lines = []
    spk_id = 0
    for n in sorted(names):
        wav = os.path.join(gt_wavs, n + ".wav")
        feat = os.path.join(feature, n + ".npy")
        f0v = os.path.join(f0, n + ".wav.npy")
        f0n = os.path.join(f0nsf, n + ".wav.npy")
        if not (os.path.exists(wav) and os.path.exists(feat) and os.path.exists(f0v) and os.path.exists(f0n)):
            continue
        lines.append(f"{wav}|{feat}|{f0v}|{f0n}|{spk_id}")

    if not lines:
        raise RuntimeError(
            f"no training rows; check feature extraction. logs_dir={logs_dir}"
        )

    # Mute lines required by RVC trainer (only append if mute assets exist).
    mute_wav = os.path.join(RVC_HOME, "logs/mute/0_gt_wavs/mute40k.wav")
    mute_feat = os.path.join(RVC_HOME, "logs/mute/3_feature768/mute.npy")
    mute_f0 = os.path.join(RVC_HOME, "logs/mute/2a_f0/mute.wav.npy")
    mute_f0nsf = os.path.join(RVC_HOME, "logs/mute/2b-f0nsf/mute.wav.npy")
    if all(os.path.exists(p) for p in [mute_wav, mute_feat, mute_f0, mute_f0nsf]):
        for _ in range(2):
            lines.append(f"{mute_wav}|{mute_feat}|{mute_f0}|{mute_f0nsf}|{spk_id}")
    else:
        _log("mute assets missing; skipping mute lines")

    with open(os.path.join(logs_dir, "filelist.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")

    _write_training_config(os.path.join(logs_dir, "config.json"))


# RVC v2 architecture config for 40 kHz training.
# Mirrors configs/v1/40k.json from the RVC repo; v2 only changes the feature
# extractor (HuBERT layer 12 -> 768-dim) which is selected at runtime via `-v v2`
# in train.py and the SynthesizerTrnMs768NSFsid model class, not in this JSON.
# The repo's main branch no longer ships configs/v2/40k.json so we inline it.
_RVC_V2_40K_CONFIG = {
    "train": {
        "log_interval": 200,
        "seed": 1234,
        "epochs": 20000,
        "learning_rate": 1e-4,
        "betas": [0.8, 0.99],
        "eps": 1e-9,
        "batch_size": 4,
        "fp16_run": True,
        "lr_decay": 0.999875,
        "segment_size": 12800,
        "init_lr_ratio": 1,
        "warmup_epochs": 0,
        "c_mel": 45,
        "c_kl": 1.0,
    },
    "data": {
        "max_wav_value": 32768.0,
        "sampling_rate": 40000,
        "filter_length": 2048,
        "hop_length": 400,
        "win_length": 2048,
        "n_mel_channels": 125,
        "mel_fmin": 0.0,
        "mel_fmax": None,
    },
    "model": {
        "inter_channels": 192,
        "hidden_channels": 192,
        "filter_channels": 768,
        "n_heads": 2,
        "n_layers": 6,
        "kernel_size": 3,
        "p_dropout": 0,
        "resblock": "1",
        "resblock_kernel_sizes": [3, 7, 11],
        "resblock_dilation_sizes": [[1, 3, 5], [1, 3, 5], [1, 3, 5]],
        "upsample_rates": [10, 10, 2, 2],
        "upsample_initial_channel": 512,
        "upsample_kernel_sizes": [16, 16, 4, 4],
        "use_spectral_norm": False,
        "gin_channels": 256,
        "spk_embed_dim": 109,
    },
}


def _write_training_config(dest_path: str) -> None:
    """Write the v2 40k training config. Prefers the repo file if it ever ships again."""
    import json
    candidates = [
        os.path.join(RVC_HOME, "configs/v2/40k.json"),
        os.path.join(RVC_HOME, "configs/v1/40k.json"),
        os.path.join(RVC_HOME, "configs/40k.json"),
    ]
    for src in candidates:
        if os.path.exists(src):
            shutil.copy(src, dest_path)
            return
    with open(dest_path, "w") as fh:
        json.dump(_RVC_V2_40K_CONFIG, fh, indent=2)


def _build_index(logs_dir: str, exp_name: str) -> None:
    """Build a faiss index from extracted HuBERT features."""
    import numpy as np
    import faiss

    feat_dir = os.path.join(logs_dir, "3_feature768")
    npys = []
    for name in sorted(os.listdir(feat_dir)):
        if name.endswith(".npy"):
            npys.append(np.load(os.path.join(feat_dir, name)))
    if not npys:
        raise RuntimeError("no feature files for index")
    big_npy = np.concatenate(npys, 0)
    # Shuffle and clamp to avoid huge indices.
    rng = np.random.default_rng(0)
    rng.shuffle(big_npy)
    if big_npy.shape[0] > 200000:
        big_npy = big_npy[:200000]

    n_ivf = min(int(16 * (big_npy.shape[0] ** 0.5)), big_npy.shape[0] // 39)
    n_ivf = max(n_ivf, 1)
    index = faiss.index_factory(768, f"IVF{n_ivf},Flat")
    index_ivf = faiss.extract_index_ivf(index)
    index_ivf.nprobe = 1
    index.train(big_npy)
    index.add(big_npy)
    out = os.path.join(logs_dir, f"{exp_name}.index")
    faiss.write_index(index, out)


def _find_latest_pth(logs_dir: str, exp_name: str) -> str | None:
    weights_dir = os.path.join(RVC_HOME, "assets/weights")
    candidates = []
    if os.path.exists(weights_dir):
        for name in os.listdir(weights_dir):
            if name.startswith(exp_name) and name.endswith(".pth"):
                candidates.append(os.path.join(weights_dir, name))
    if candidates:
        candidates.sort(key=os.path.getmtime, reverse=True)
        return candidates[0]
    # Fallback: search logs_dir
    for name in os.listdir(logs_dir):
        if name.endswith(".pth"):
            return os.path.join(logs_dir, name)
    return None


def _find_index(logs_dir: str) -> str | None:
    for name in os.listdir(logs_dir):
        if name.endswith(".index"):
            return os.path.join(logs_dir, name)
    return None
