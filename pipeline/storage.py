"""R2 (S3-compatible) upload for finished videos.

Keys include a random token so URLs are unguessable — the old n8n flow used
{discord_id}{job_id}, which was enumerable and could even collide.
boto3 is sync; calls run in a worker thread.
"""
from __future__ import annotations

import asyncio
import functools
import secrets

import boto3

from config import cfg

_client = None


def _s3():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=cfg.r2_endpoint,
            aws_access_key_id=cfg.r2_access_key_id,
            aws_secret_access_key=cfg.r2_secret_access_key,
            region_name="auto",
        )
    return _client


def video_key(discord_id: int, job_id: int) -> str:
    return f"videos/{discord_id}/{job_id}-{secrets.token_urlsafe(9)}.mp4"


async def upload_video(data: bytes, key: str) -> str:
    """Upload and return the public URL."""
    fn = functools.partial(
        _s3().put_object,
        Bucket=cfg.r2_bucket,
        Key=key,
        Body=data,
        ContentType="video/mp4",
    )
    await asyncio.to_thread(fn)
    return f"{cfg.r2_public_base_url.rstrip('/')}/{key}"
