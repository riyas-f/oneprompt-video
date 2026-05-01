"""Suby API integration: checkout sessions + webhook signature verification.

Per https://documentation.suby.fi/api-reference/introduction the API is REST
with bearer auth. Field names below match the documented schema; if Suby's
production endpoint differs, this is the only file that needs to change.
"""
from __future__ import annotations

import hmac
import hashlib

import httpx

from config import cfg


async def create_checkout_session(discord_id: int, discord_username: str) -> tuple[str, str]:
    """Create a Suby subscription checkout. Returns (checkout_url, payment_id)."""
    # Discord does not expose real emails. Suby requires customerEmail, so we
    # send a deterministic placeholder; Suby will let the user override it on
    # the checkout page.
    placeholder_email = f"discord+{discord_id}@oneprompt.app"

    payload = {
        "productId": cfg.suby_product_id,
        "customerEmail": placeholder_email,
        "customerFirstName": discord_username[:32] or "Discord",
        "customerLastName": "User",
        "externalRef": str(discord_id),
        "successUrl": cfg.suby_success_url,
        "cancelUrl": cfg.suby_cancel_url,
        "metadata": {
            "discord_id": str(discord_id),
            "discord_username": discord_username,
        },
    }
    # Suby rejects priceCents/currency for fixed-price products. Only send
    # them when the user is using a custom-price product (SUBY_PRICE_CENTS > 0).
    if cfg.suby_price_cents > 0:
        payload["priceCents"] = str(cfg.suby_price_cents)
        payload["currency"] = cfg.suby_currency
    headers = {
        "X-Suby-Api-Key": cfg.suby_api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{cfg.suby_api_base}/api/subscription/create",
            json=payload,
            headers=headers,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Suby {r.status_code}: {r.text[:400]}")
        body = r.json()

    data = body.get("data") or {}
    url = data.get("paymentUrl") or body.get("paymentUrl")
    payment_id = data.get("paymentId") or body.get("paymentId") or ""
    if not url:
        raise RuntimeError(f"Suby returned no paymentUrl: {body}")
    if not payment_id:
        raise RuntimeError(f"Suby returned no paymentId: {body}")
    return url, payment_id


def compute_signature(raw_body: bytes, timestamp: str | None = None) -> str:
    """Suby signs `<timestamp>.<body>` with HMAC-SHA256, hex-encoded."""
    payload = (timestamp.encode() + b"." + raw_body) if timestamp else raw_body
    return hmac.new(
        cfg.suby_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    timestamp: str | None = None,
) -> bool:
    """Verify `X-Webhook-Signature: v1=<hex>` over `<timestamp>.<body>`."""
    if not signature_header:
        return False
    sig = signature_header.strip()
    sig = sig.removeprefix("v1=").removeprefix("sha256=").strip()
    expected = compute_signature(raw_body, timestamp)
    return hmac.compare_digest(expected, sig)
