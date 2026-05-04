"""Run `modal deploy` per-user with the user's tokens, parse the deployed URL."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys

from config import cfg

_URL_RE = re.compile(r"https://[a-z0-9\-]+\.modal\.run[^\s]*")


def _deploy_blocking(token_id: str, token_secret: str) -> str:
    env = os.environ.copy()
    env["MODAL_TOKEN_ID"] = token_id
    env["MODAL_TOKEN_SECRET"] = token_secret
    # Force UTF-8 in the child process so Modal's Unicode CLI output doesn't
    # crash on Windows hosts where the default codec is cp1252. No-op on Linux.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # First-time deploys build the full CUDA image (~3-5 min) before pushing.
    # Re-deploys for the same user reuse the cached image and finish in ~30s.
    # 10 minutes covers both with headroom.
    result = subprocess.run(
        [sys.executable, "-m", "modal", "deploy", cfg.modal_app_path],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )

    if result.returncode != 0:
        # Don't leak credentials in error text. Both stderr and stdout from modal
        # CLI are safe — the build-log URL is usually only printed to stdout.
        import logging
        logging.getLogger(__name__).error(
            "modal deploy rc=%s\nSTDOUT:\n%s\nSTDERR:\n%s",
            result.returncode, result.stdout, result.stderr,
        )
        tail = (result.stderr.strip() + "\n" + result.stdout.strip()).strip()[-1200:]
        raise RuntimeError(f"modal deploy failed:\n{tail}")

    urls = _URL_RE.findall(result.stdout)
    if not urls:
        raise RuntimeError("Could not find deployed URL in modal output")

    # Modal sometimes prints multiple URLs (web endpoint, dashboard). The
    # *.modal.run pattern should already exclude the dashboard, but pick the
    # longest match to favor the full webhook URL with path.
    return max(urls, key=len)


async def deploy_modal_app(token_id: str, token_secret: str) -> str:
    return await asyncio.to_thread(_deploy_blocking, token_id, token_secret)
