# Hebrew Voice Bot — Telegram + ElevenLabs

A Telegram bot that converts text to speech (Hebrew female voice) and converts voice messages to a female voice using ElevenLabs.

- **Send text** -> bot replies with a spoken voice message
- **Send a voice recording** -> bot converts it to a female voice and sends it back

## Modes

`/settings` lets each user pick between two modes:

- **Casual** — Default. Uses ElevenLabs *Instant Voice Clone* (IVC). Fast, only needs a few seconds of audio, voice is ready immediately.
- **Premium** — Uses ElevenLabs *Professional Voice Clone* (PVC). Requires at least 30 minutes of clean audio per voice, an in-Telegram captcha verification step, and waiting hours for fine-tuning to complete. The result is the highest-fidelity clone ElevenLabs offers.

PVC requires the ElevenLabs Creator tier or higher (each PVC slot eats into the account's professional-voice quota). The bot polls training status every 5 minutes via a JobQueue and DMs the owner when each Premium voice is ready.

## Alternative voice engines

ElevenLabs is the baseline. These engines run next to it so we can pick
whichever sounds most natural in Hebrew:

| Engine | Text → voice | Recording → voice | Needs |
|--------|:---:|:---:|-------|
| ElevenLabs | ✅ v3 | ✅ multilingual STS | `ELEVENLABS_API_KEY` |
| MiniMax Speech 2.8 HD | ✅ (Hebrew `language_boost`) | — | `MINIMAX_API_KEY` |
| ModelsLab | ✅ `text_to_audio` (hebrew) | ✅ `voice_to_voice` | `MODELSLAB_API_KEY` |
| Applio (RVC) | ✅ MiniMax → Applio | ✅ trained per voice | Modal tokens |
| Seed-VC | ✅ MiniMax → Seed-VC | ✅ zero-shot | Modal tokens |

Bot commands:

- `/compare`: toggles comparison mode. Every text or voice message comes back
  once per engine, numbered, with the engine name and how long it took.
- `/engines`: picks the engine used outside comparison mode.
- `/prepare`: sets the active voice up for every engine. `/newvoice` does this
  automatically.

What `/newvoice` prepares, in the background, after the ElevenLabs clone:

1. Stores a reference clip of up to 28 s in object storage. Seed-VC and ModelsLab
   use it, and system voices fall back to the ElevenLabs preview.
2. Creates a MiniMax clone. MiniMax deletes clones unused for 7 days, and the bot
   re-clones automatically.
3. Starts Applio training on Modal (about 20-40 min). The bot DMs you when it's
   ready.

Code: `engines/` holds the API clients, `voice_lab.py` holds the routing,
preparation and comparison, and `gpu_service/voice_lab.py` is the Modal app
for Applio and Seed-VC.

### GPU service (Modal)

```bash
pip install modal
modal token new                       # once; writes ~/.modal.toml
modal deploy gpu_service/voice_lab.py # rebuild after changing it
```

Copy `token_id` / `token_secret` from `~/.modal.toml` into the bot's `.env` as
`MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`. GPUs (A10G) are billed per second, and
nothing stays warm when idle. Expect a 30-90 s cold start on the first request.
Tuning knobs are in `.env.example` (`APPLIO_*`, `SEEDVC_*`).

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
