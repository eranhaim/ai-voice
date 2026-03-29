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

## Deploy to AWS EC2 with Docker

### 1. Launch an EC2 Instance

- Go to **AWS Console > EC2 > Launch Instance**
- **AMI:** Amazon Linux 2023
- **Instance type:** `t3.micro` (free tier eligible, plenty for this bot)
- **Key pair:** Create or select one (you'll need the `.pem` file to SSH in)
- **Security group:** Allow SSH (port 22) from your IP
- **Storage:** 8 GB default is fine
- Click **Launch Instance**

### 2. SSH into Your Instance

```bash
chmod 400 your-key.pem
ssh -i your-key.pem ec2-user@<your-ec2-public-ip>
```

### 3. Install Docker

```bash
sudo yum update -y
sudo yum install -y docker git
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
```

Log out and back in for the docker group to take effect:

```bash
exit
ssh -i your-key.pem ec2-user@<your-ec2-public-ip>
```

### 4. Get the Code onto EC2

**Option A — Git (if you pushed to a repo):**

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

**Option B — SCP (copy files directly):**

From your local machine:

```bash
scp -i your-key.pem -r "C:\Users\Eran\Desktop\AI OF voice" ec2-user@<your-ec2-public-ip>:~/voice-bot
```

Then on EC2:

```bash
cd ~/voice-bot
```

### 5. Create the `.env` File on EC2

```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=XB0fDUnXU5powFXDhCwa
EOF
```

Replace the values with your actual keys.

### 6. Build and Run the Docker Container

```bash
docker build -t voice-bot .
docker run -d --name voice-bot --restart unless-stopped --env-file .env voice-bot
```

That's it — the bot is running.

### 7. Useful Commands

```bash
# Check if the container is running
docker ps

# View live logs
docker logs -f voice-bot

# Stop the bot
docker stop voice-bot

# Start it again
docker start voice-bot

# Restart after code changes
docker stop voice-bot && docker rm voice-bot
docker build -t voice-bot .
docker run -d --name voice-bot --restart unless-stopped --env-file .env voice-bot
```

### 8. Auto-restart on Reboot

The `--restart unless-stopped` flag in the run command means Docker will automatically restart the bot if the EC2 instance reboots. Docker itself starts on boot because of the `systemctl enable docker` step.

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
