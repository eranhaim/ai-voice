# Hebrew Voice Bot — Telegram + ElevenLabs

A Telegram bot that converts text to speech (Hebrew female voice) and converts voice messages to a female voice using ElevenLabs.

- **Send text** -> bot replies with a spoken voice message
- **Send a voice recording** -> bot converts it to a female voice and sends it back

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

### Available Free-Tier Voices

| Name | Voice ID | Style |
|------|----------|-------|
| Charlotte | `XB0fDUnXU5powFXDhCwa` | Seductive, young female |
| Rachel | `21m00Tcm4TlvDq8ikWAM` | Calm, warm female |
| Alice | `Xb7hH8MSUJpSbSDYk0k2` | Confident, British female |
