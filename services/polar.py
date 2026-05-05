"""Polar.sh API integration: checkout sessions + webhook signature verification.

Docs:
  Checkout API:    https://docs.polar.sh/api-reference/checkouts/create-session
  Webhook events:  https://docs.polar.sh/integrate/webhooks/events
  Standard Webhooks signature spec: https://www.standardwebhooks.com/

Polar uses the Standard Webhooks signature scheme. The webhook secret is
shaped like `polar_whs_<base64>`; the binary HMAC key is the base64-decoded
portion after the prefix.
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import time

import httpx

from config import cfg

POLAR_API_BASE = "https://api.polar.sh"

# Allow up to 5 minutes of clock skew between Polar and our server.
WEBHOOK_TOLERANCE_SECONDS = 5 * 60


async def create_checkout_session(discord_id: int, discord_username: str) -> tuple[str, str]:
    """Create a Polar subscription checkout. Returns (checkout_url, checkout_id).

    Trial length is configured on the Polar product itself (7 days for
    OnePrompt Pro). The checkout inherits it automatically.
    """
    placeholder_email = f"discord+{discord_id}@oneprompt.app"

    payload: dict = {
        "products": [cfg.polar_product_id],
        "customer_email": placeholder_email,
        "customer_name": (discord_username[:64] or "Discord User"),
        "external_customer_id": str(discord_id),
        "metadata": {
            "discord_id": str(discord_id),
            "discord_username": discord_username,
        },
    }
    if cfg.polar_success_url:
        payload["success_url"] = cfg.polar_success_url

    headers = {
        "Authorization": f"Bearer {cfg.polar_access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{cfg.polar_api_base}/v1/checkouts/",
            json=payload,
            headers=headers,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Polar {r.status_code}: {r.text[:400]}")
        body = r.json()

    url = body.get("url")
    checkout_id = body.get("id") or ""
    if not url:
        raise RuntimeError(f"Polar returned no checkout url: {body}")
    if not checkout_id:
        raise RuntimeError(f"Polar returned no checkout id: {body}")
    return url, checkout_id


def _decode_secret(secret: str) -> bytes:
    """Strip the `polar_whs_`/`whsec_` prefix and base64-decode the secret."""
    body = secret.split("_", 2)[-1] if "_" in secret else secret
    body += "=" * (-len(body) % 4)  # restore base64 padding
    return base64.b64decode(body)


def verify_webhook_signature(
    raw_body: bytes,
    msg_id: str | None,
    msg_timestamp: str | None,
    signature_header: str | None,
) -> bool:
    """Verify a Standard Webhooks signed Polar request.

    `signature_header` may contain multiple space-separated `v1,<base64>`
    signatures (during key rotation). Any one matching is enough.
    """
    if not (msg_id and msg_timestamp and signature_header):
        return False

    try:
        ts = int(msg_timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > WEBHOOK_TOLERANCE_SECONDS:
        return False

    try:
        secret_bytes = _decode_secret(cfg.polar_webhook_secret)
    except Exception:
        return False

    signed = f"{msg_id}.{msg_timestamp}.".encode() + raw_body
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed, hashlib.sha256).digest()
    ).decode()

    for part in signature_header.split(" "):
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(expected, sig):
            return True
    return False
