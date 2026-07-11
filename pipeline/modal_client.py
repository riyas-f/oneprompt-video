"""HTTP client for the Modal video app (async job API, with sync fallback).

The async endpoints (/generate-video-async, /job/...) exist in the updated
main.py. Studio users may still be running an older deploy without them, so
submit() falls back to the legacy synchronous /generate-video on 404/405.
"""
from __future__ import annotations

import logging

import httpx

from config import cfg

log = logging.getLogger(__name__)


def _auth_headers() -> dict:
    return {"X-OnePrompt-Key": cfg.render_api_key} if cfg.render_api_key else {}

_SHORT = httpx.Timeout(30.0)
# Sync fallback holds the connection for the whole render.
_SYNC = httpx.Timeout(60.0 * 35, connect=30.0)
# Result download: ~50 MB from Modal.
_RESULT = httpx.Timeout(60.0 * 5, connect=30.0)


class ModalClient:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_SHORT) as c:
                r = await c.get(f"{self.base}/")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def submit(self, scenes: list[dict], voice: str, job_key: str,
                     tts_speed: float = 1.0,
                     characters: dict | None = None) -> str:
        """Submit a render. Returns 'async' or 'sync' (caller picks the wait path)."""
        payload = {
            "scenes": scenes,
            "voice": voice,
            "tts_speed": tts_speed,
            "job_key": job_key,
            "characters": characters or None,
        }
        async with httpx.AsyncClient(timeout=_SHORT) as c:
            r = await c.post(f"{self.base}/generate-video-async", json=payload,
                             headers=_auth_headers())
            if r.status_code in (404, 405):
                log.info("Modal app at %s lacks async API — using sync fallback", self.base)
                return "sync"
            r.raise_for_status()
            return "async"

    async def status(self, job_key: str) -> dict:
        """{status: queued|running|done|failed|unknown, scene?, total?, error?}"""
        async with httpx.AsyncClient(timeout=_SHORT) as c:
            r = await c.get(f"{self.base}/job/{job_key}/status", headers=_auth_headers())
            if r.status_code == 404:
                return {"status": "unknown"}
            r.raise_for_status()
            return r.json()

    async def fetch_result(self, job_key: str) -> bytes:
        async with httpx.AsyncClient(timeout=_RESULT) as c:
            r = await c.get(f"{self.base}/job/{job_key}/result", headers=_auth_headers())
            r.raise_for_status()
            return r.content

    async def cleanup(self, job_key: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=_SHORT) as c:
                await c.delete(f"{self.base}/job/{job_key}", headers=_auth_headers())
        except httpx.HTTPError:
            log.warning("Modal job cleanup failed for %s (non-fatal)", job_key)

    async def generate_sync(self, scenes: list[dict], voice: str,
                            tts_speed: float = 1.0) -> bytes:
        """Legacy path: block on /generate-video until the mp4 comes back."""
        payload = {"scenes": scenes, "voice": voice, "tts_speed": tts_speed}
        async with httpx.AsyncClient(timeout=_SYNC) as c:
            r = await c.post(f"{self.base}/generate-video", json=payload)
            r.raise_for_status()
            return r.content
