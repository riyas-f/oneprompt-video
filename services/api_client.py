"""Calls into the OnePrompt API server (the AI agent + S3 uploader)."""
from __future__ import annotations

import httpx

from config import cfg


async def request_video(
    *,
    content: str,
    modal_url: str,
    extra_context: str,
    discord_id: int,
    job_id: int,
) -> dict:
    """Fire-and-forget: API server should respond <2s with `{message, api_job_id}`."""
    payload = {
        "content": content,
        "modal_url": modal_url,
        "extra_context": extra_context,
        "discord_id": str(discord_id),
        "job_id": job_id,
        "callback_url": f"{cfg.bot_public_url.rstrip('/')}/webhooks/video-ready",
        "callback_secret": cfg.video_api_callback_secret,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(cfg.video_api_url, json=payload)
        r.raise_for_status()
        # n8n sometimes returns text/plain or a list. Be lenient.
        try:
            data = r.json()
        except Exception:
            data = {"message": r.text}
        if isinstance(data, list) and data:
            data = data[0]
        return data if isinstance(data, dict) else {"message": str(data)}
