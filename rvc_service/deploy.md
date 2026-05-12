# RVC Service (Modal) — Deployment

This is the heavy-duty Premium mode voice service. It runs on Modal serverless
GPUs (A10G) and exposes two functions to the bot:

- `train_voice(voice_id, sample_urls, total_epoch=150)` — async, ~20-60 min.
- `convert(voice_id, audio_bytes, f0_up_key=0)` — sync, ~3-10 s warm.

## One-time setup

### 1. Modal account

```bash
pip install modal
modal token new
```

This creates `~/.modal.toml` with `token_id` + `token_secret`. Copy these
values into the bot's `.env` as `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`
(so the bot container can call Modal without browser auth).

### 2. Create the `ai-voice` secret in Modal

The Modal functions need the same MongoDB and AWS credentials the bot uses.
Easiest: create them in the Modal dashboard or via CLI:

```bash
modal secret create ai-voice \
  MONGO_URI=<your mongo uri> \
  MONGO_DB=voice_bot \
  AWS_ACCESS_KEY_ID=<...> \
  AWS_SECRET_ACCESS_KEY=<...> \
  AWS_REGION=us-east-1 \
  AWS_S3_BUCKET=<your bucket>
```

### 3. Deploy

From the project root:

```bash
modal deploy rvc_service/app.py
```

First deploy takes 10-20 min because the image downloads ~2 GB of RVC
pretrained weights.

## Updates

After editing `rvc_service/*.py`:

```bash
modal deploy rvc_service/app.py
```

Re-runs only rebuild changed layers, so subsequent deploys are fast unless
the apt or pip install step changed.

## Costs (rough)

- Training one voice on A10G (~30 min): $0.50-$0.80.
- Inference, warm A10G: ~$0.01-$0.03 per message.
- `min_containers=1` on `convert` keeps one A10G warm (~$26/mo) so users
  don't see 30 s cold starts.

To save money during low usage, drop `min_containers=1` from `app.py` and
redeploy.

## Troubleshooting

- Logs: `modal app logs rvc-voice`.
- Re-run a function manually: `modal run rvc_service/app.py::convert ...`.
- Persistent model cache: `modal volume ls rvc-models`.
- Delete one trained voice: `modal volume rm rvc-models <voice_id>`.
