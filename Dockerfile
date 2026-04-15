FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db.py .
COPY s3.py .
COPY bot.py .

CMD ["python", "bot.py"]
