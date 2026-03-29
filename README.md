# Hebrew Text-to-Speech — Telegram Bot & CLI

AI-powered Hebrew text-to-speech using Microsoft's neural voice engine. Generates natural-sounding female voice messages (HilaNeural) that you can send directly through Telegram.

**Free — no API keys or paid services required for TTS.**

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the bot token you receive

### 3. Configure

Create a `.env` file (or copy from `.env.example`):

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

### 4. Run the bot

```bash
python bot.py
```

Now open your bot in Telegram and send it any Hebrew text — it will reply with a voice message.

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and help |
| `/rate <value>` | Adjust speed — e.g. `/rate -10%` (slower) or `/rate +20%` (faster) |
| `/pitch <value>` | Adjust pitch — e.g. `/pitch +5Hz` (higher) or `/pitch -3Hz` (lower) |
| `/file` | Toggle also sending audio as a downloadable MP3 file |
| `/settings` | Show current voice settings |
| `/reset` | Reset all settings to defaults |

---

## CLI Usage (no Telegram needed)

Generate audio files directly from the command line:

```bash
# Basic usage
python generate.py "שלום, מה שלומך היום?"

# Custom output file
python generate.py "שלום עולם" -o hello.mp3

# Adjust rate and pitch
python generate.py "הודעה חשובה" -o message.mp3 -r -10% -p +2Hz
```

### CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `text` | (required) | Hebrew text to convert |
| `-o, --output` | `output.mp3` | Output file path |
| `-r, --rate` | `-5%` | Speech rate (e.g. `-10%`, `+20%`) |
| `-p, --pitch` | `+0Hz` | Pitch (e.g. `+5Hz`, `-3Hz`) |

---

## Voice Details

- **Voice:** `he-IL-HilaNeural` — Microsoft's neural Hebrew female voice
- **Engine:** Edge TTS (free, no API key)
- **Output:** MP3 audio
- **Default rate:** Slightly slower than standard (`-5%`) for a more natural conversational tone

---

## Tips for Natural-Sounding Results

- **Slow down slightly** — `/rate -10%` sounds more conversational
- **Add punctuation** — commas and periods create natural pauses
- **Keep sentences short** — sounds more natural than long paragraphs
- **Use the pitch control** — slight pitch adjustments can add personality
