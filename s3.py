import os

import boto3

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
    return _client


def upload_sample(telegram_id: int, filename: str, audio_bytes: bytes) -> str:
    bucket = os.getenv("AWS_S3_BUCKET")
    key = f"voices/{telegram_id}/{filename}"
    _get_client().put_object(Bucket=bucket, Key=key, Body=audio_bytes)
    return f"s3://{bucket}/{key}"


def delete_samples(urls: list[str]) -> None:
    client = _get_client()
    for url in urls:
        parts = url.replace("s3://", "").split("/", 1)
        if len(parts) == 2:
            client.delete_object(Bucket=parts[0], Key=parts[1])
