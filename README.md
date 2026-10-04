# Hebrew Voice Bot — Telegram + ElevenLabs V4

A Telegram bot that generates Hebrew speech and converts authorized source audio with ElevenLabs.

- **Send text** -> bot replies with a spoken voice message
- **Send a voice recording** -> bot converts it to a female voice and sends it back

## Voice engine and authorization

`/settings` lets each user pick between two modes:

- **Casual** — Instant Voice Clone (IVC), requiring at least one minute of clean,
  single-speaker reference audio.
- **Premium** — Professional Voice Clone (PVC), requiring at least 20 minutes of
  clean references plus ElevenLabs identity verification and asynchronous training.

Text and dialogue generation use ElevenLabs `eleven_v4`. V4 is a speech-synthesis
model, not a voice-enrollment API. Existing voices without recorded consent are blocked.

Only create, import, remix, or generate with a creator voice after its explicit consent
has been recorded. New reference audio is validated before enrollment and is not retained by
this application or uploaded to S3. Never commit or log source audio, consent records,
API keys, or voice data.

---

## Run Locally

```bash
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):

```
TELEGRAM_BOT_TOKEN=your_token
ELEVENLABS_API_KEY=your_key
ELEVENLABS_MODEL=eleven_v4
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
| `ELEVENLABS_MODEL` | `eleven_v4` for text and dialogue synthesis |

### Available Free-Tier Voices

| Name | Voice ID | Style |
|------|----------|-------|
| Charlotte | `XB0fDUnXU5powFXDhCwa` | Seductive, young female |
| Rachel | `21m00Tcm4TlvDq8ikWAM` | Calm, warm female |
| Alice | `Xb7hH8MSUJpSbSDYk0k2` | Confident, British female |
