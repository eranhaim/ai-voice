# Hebrew Voice Bot — Telegram + ElevenLabs

A Telegram bot that converts text to speech (Hebrew female voice) and converts voice messages to a female voice using ElevenLabs.

- **Send text** -> bot replies with a spoken voice message
- **Send a voice recording** -> bot converts it to a female voice and sends it back

Custom voices use ElevenLabs *Instant Voice Clone* (IVC), which is ready immediately after the user uploads samples.

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

## Deploy to Vultr

The app runs on a Vultr VPS (Tel Aviv, `64.176.175.97`) in `/home/deploy/projects/ai-voice`.
Caddy terminates TLS and proxies to the admin panel on `127.0.0.1:8086`
(`docker-compose.vultr.yml` overrides the web port). The server is shared with other
projects, so only ever touch the `ai-voice-*` containers.

### 1. SSH in

```powershell
ssh -i key root@64.176.175.97
```

`key` is the private SSH key (git-ignored; never commit it).

### 2. Update the code

```bash
cd /home/deploy/projects/ai-voice
git fetch origin
git checkout <branch> && git pull
```

### 3. Configure `.env` on the server

Copy `.env.example` to `.env` and fill in the values. Voice-sample and run-audio
storage uses Vultr Object Storage (S3-compatible):

```
S3_ENDPOINT_URL=https://ams1.vultrobjects.com
AWS_ACCESS_KEY_ID=<object storage access key>
AWS_SECRET_ACCESS_KEY=<object storage secret key>
AWS_S3_BUCKET=voice-ai-bucket
AWS_REGION=us-east-1
```

Leave `S3_ENDPOINT_URL` empty to use AWS S3 instead. Sample backup is best-effort:
if storage is unreachable, voice cloning still succeeds.

### 4. Build and run

```bash
docker compose -f docker-compose.yml -f docker-compose.vultr.yml up -d --build
```

### 5. Useful commands

```bash
docker compose logs -f bot     # live bot logs
docker compose ps              # status
docker compose down            # stop (this project only)
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
