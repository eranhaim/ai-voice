FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db.py .
COPY s3.py .
COPY elevenlabs_pvc.py .
COPY minimax_tts.py .
COPY pitch.py .
COPY bot.py .

CMD ["python", "bot.py"]
