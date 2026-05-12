# Hebrew Voice Bot — Telegram + ElevenLabs + RVC

A Telegram bot that converts text to speech and converts voice messages to a chosen voice. Two modes:

- **Casual** (default) — ElevenLabs TTS + STS. Fast, instant clones, decent similarity.
- **Premium** — RVC on Modal GPUs. Long training (~20-60 min per voice) but near-perfect similarity. TTS chains ElevenLabs -> RVC.

Customers toggle modes from `/settings`. See [rvc_service/deploy.md](rvc_service/deploy.md) for Premium setup.

---

## Run Locally

```bash
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):

```
TELEGRAM_BOT_TOKEN=your_token
ELEVENLABS_API_KEY=your_key
ELEVENLABS_VOICE_ID=XB0fDUnXU5powFXDhCwa
```

```bash
python bot.py
```

---

## Deploy to EC2

### 1. SSH into the Instance

From your local machine (Windows):

```powershell
ssh -i key.pem ubuntu@54.173.144.0
```

### 2. Copy the Project to EC2

From your local machine, open a second terminal:

```powershell
scp -i key.pem -r "C:\Users\Eran\Desktop\AI OF voice" ubuntu@54.173.144.0:~/voice-bot
```

### 3. Create the `.env` File on EC2

```bash
cd ~/voice-bot

cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=XB0fDUnXU5powFXDhCwa
EOF
```

Replace the values with your actual keys.

### 4. Build and Run

```bash
docker compose up -d --build
```

That's it — the bot is running.

### 5. Useful Commands

```bash
# View live logs
docker compose logs -f

# Stop the bot
docker compose down

# Rebuild after code changes
docker compose up -d --build

# Check status
docker compose ps
```

---

## Configuration

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `ELEVENLABS_API_KEY` | API key from elevenlabs.io |
| `ELEVENLABS_VOICE_ID` | Voice to use (see below) |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | Optional — required for Premium mode |
| `RVC_SOURCE_VOICE_ID` | Optional — neutral EL voice used as TTS source in Premium |

## Premium mode (RVC)

Premium uses a separate Modal app for GPU training/inference. One-time setup:

```bash
pip install modal
modal token new
modal secret create ai-voice MONGO_URI=... AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_S3_BUCKET=... AWS_REGION=us-east-1
modal deploy rvc_service/app.py
```

Then put the same `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` into the bot's `.env` and redeploy. Full details in [rvc_service/deploy.md](rvc_service/deploy.md).

### Available Free-Tier Voices

| Name | Voice ID | Style |
|------|----------|-------|
| Charlotte | `XB0fDUnXU5powFXDhCwa` | Seductive, young female |
| Rachel | `21m00Tcm4TlvDq8ikWAM` | Calm, warm female |
| Alice | `Xb7hH8MSUJpSbSDYk0k2` | Confident, British female |
