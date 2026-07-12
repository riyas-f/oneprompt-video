"""aiohttp server for Polar + API-server + Discord callbacks. Runs in the bot's event loop."""
from __future__ import annotations

import hmac
import hashlib
import logging

import discord
from aiohttp import web
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cogs.install_listener import handle_install
from config import cfg
from database import models as db
from services import embeds
from services.polar import verify_webhook_signature as verify_polar_signature

log = logging.getLogger(__name__)


def _verify_callback_signature(raw: bytes, header: str | None) -> bool:
    if not header:
        return False
    expected = hmac.new(
        cfg.video_api_callback_secret.encode(),
        raw,
        hashlib.sha256,
    ).hexdigest()
    sig = header.removeprefix("sha256=").strip()
    return hmac.compare_digest(expected, sig)


def build_app(bot: discord.Client) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def polar_webhook(request: web.Request) -> web.Response:
        """Polar.sh subscription webhook. Standard Webhooks signed.

        Events handled:
          subscription.created  → mark Pro (covers trialing & active)
          subscription.updated  → re-evaluate status
          subscription.canceled → mark cancelled
          order.refunded        → mark cancelled
        Other events are logged and ignored.

        Discord identity is resolved from `data.metadata.discord_id` (set in
        services/polar.create_checkout_session). Falls back to the
        pending_checkouts table via the checkout id when metadata is missing.
        """
        raw = await request.read()
        msg_id = request.headers.get("webhook-id")
        msg_ts = request.headers.get("webhook-timestamp")
        msg_sig = request.headers.get("webhook-signature")

        if not verify_polar_signature(raw, msg_id, msg_ts, msg_sig):
            log.warning(
                "Polar webhook signature mismatch. id=%s ts=%s sig=%s body[:200]=%r",
                msg_id, msg_ts, msg_sig, raw[:200],
            )
            return web.json_response({"error": "bad signature"}, status=401)

        try:
            event = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        etype = event.get("type") or ""
        data = event.get("data") or {}
        meta = data.get("metadata") or {}

        # Resolve Discord identity. Primary: metadata.discord_id we set at
        # checkout. Fallback: external_customer_id (also set to discord_id).
        discord_id: int | None = None
        for candidate in (meta.get("discord_id"), data.get("external_customer_id")):
            if candidate is None:
                continue
            try:
                discord_id = int(candidate)
                break
            except (TypeError, ValueError):
                continue

        # Last resort: lookup by checkout id (when present on the payload).
        if discord_id is None:
            checkout_id = data.get("checkout_id") or data.get("checkout", {}).get("id")
            if checkout_id:
                discord_id = await db.lookup_discord_id_by_payment(str(checkout_id))

        if discord_id is None:
            log.info("Polar %s ignored — no discord_id resolvable (likely dashboard test)", etype)
            return web.json_response({"ok": True})

        if etype in ("subscription.created", "subscription.updated"):
            status = (data.get("status") or "").lower()
            customer_id = str(data.get("customer_id") or (data.get("customer") or {}).get("id") or "")
            sub_id = str(data.get("id") or "")
            checkout_id = data.get("checkout_id") or (data.get("checkout") or {}).get("id")

            if status in ("active", "trialing"):
                user_row = await db.get_user(discord_id)
                already_pro = bool(user_row and user_row["subscription_status"] == "pro")
                username = meta.get("discord_username") or (user_row or {}).get("discord_username") or "user"
                await db.upsert_user(discord_id, username)
                await db.mark_subscribed(discord_id, customer_id, sub_id)

                # Pick tier from product_id. Default to Pro if anything else is set.
                product_id = str(
                    data.get("product_id")
                    or (data.get("product") or {}).get("id")
                    or ""
                )
                tier = "studio" if product_id == cfg.polar_product_id_studio else "pro"

                # Reset hosted-Modal counter to align with the new period.
                # Use Polar's current_period_end if present; else fall back to +30d.
                from datetime import datetime, timedelta, timezone
                period_end_raw = data.get("current_period_end")
                quota_reset_at = None
                if period_end_raw:
                    try:
                        quota_reset_at = datetime.fromisoformat(
                            period_end_raw.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except Exception:
                        quota_reset_at = None
                if quota_reset_at is None:
                    quota_reset_at = datetime.utcnow() + timedelta(days=30)
                await db.set_tier(discord_id, tier, quota_reset_at, is_trialing=(status == "trialing"))

                if checkout_id:
                    await db.delete_pending_checkout(str(checkout_id))
                if not already_pro:
                    trialing = status == "trialing"
                    if tier == "studio":
                        title = "Welcome to OnePrompt Studio"
                        body = (
                            f"**{cfg.studio_monthly_minutes} render-minutes / month**, "
                            "fully hosted, priority rendering — zero setup.\n\n"
                            "Just run `/create` and you're off."
                        )
                    else:
                        title = (
                            "Your free trial has started"
                            if trialing else "You're subscribed to OnePrompt Pro"
                        )
                        quota_label = (
                            f"**{cfg.trial_minutes} render-minutes** during your "
                            f"{cfg.polar_trial_days}-day trial"
                            if trialing else
                            f"**{cfg.pro_monthly_minutes} render-minutes / month**"
                        )
                        body = (
                            f"{quota_label}, fully hosted — no setup needed.\n\n"
                            "Just run `/create` and you're off. Need more volume? "
                            "**Studio** has "
                            f"{cfg.studio_monthly_minutes} render-minutes / month."
                        )
                    await _dm(bot, discord_id, embeds.success(title, body))
            else:
                # past_due, unpaid, canceled, incomplete, etc.
                await db.mark_cancelled(discord_id)
                if status == "past_due":
                    await _dm(
                        bot, discord_id,
                        embeds.error(
                            "Payment failed",
                            "Your latest charge didn't go through. Please update your card or use `/subscribe`.",
                        ),
                    )

        elif etype in ("subscription.canceled", "subscription.revoked"):
            await db.mark_cancelled(discord_id)
            await _dm(
                bot, discord_id,
                embeds.info(
                    "Subscription cancelled",
                    "Your OnePrompt subscription has ended. Use `/subscribe` anytime to reactivate.",
                ),
            )

        elif etype == "order.refunded":
            await db.mark_cancelled(discord_id)
            await _dm(
                bot, discord_id,
                embeds.info(
                    "Refund processed",
                    "Your payment was refunded and your subscription is no longer active.",
                ),
            )

        else:
            log.info("Ignoring Polar event: %s", etype)

        return web.json_response({"ok": True})

    async def video_ready(request: web.Request) -> web.Response:
        raw = await request.read()
        if not _verify_callback_signature(raw, request.headers.get("X-Signature")):
            return web.json_response({"error": "bad signature"}, status=401)

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        try:
            discord_id = int(payload["discord_id"])
            job_id = int(payload["job_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "missing fields"}, status=400)

        status = payload.get("status", "done")

        if status == "done":
            video_url = payload.get("video_url")
            if not video_url:
                return web.json_response({"error": "missing video_url"}, status=400)
            row = await db.complete_job(job_id, video_url)
            if row is None:
                # Idempotent: already terminal.
                return web.json_response({"ok": True, "noop": True})
            await _dm(
                bot, discord_id,
                embeds.success(
                    "Your video is ready",
                    f"**Prompt:** {row['prompt']}\n\n[▶ Watch / download]({video_url})",
                ),
            )
        else:
            err = payload.get("error_message", "Unknown error")
            row = await db.fail_job(job_id, err)
            if row is None:
                return web.json_response({"ok": True, "noop": True})
            await _dm(
                bot, discord_id,
                embeds.error(
                    "Video generation failed",
                    f"`{err[:300]}`\n\nTry `/create` again, or use `/support` if it persists.",
                ),
            )

        return web.json_response({"ok": True})

    async def discord_webhook(request: web.Request) -> web.Response:
        """Discord application webhook events (e.g. APPLICATION_AUTHORIZED).

        Setup: Discord Developer Portal → Your App → "Webhook Events" →
        "Use Webhook Events Endpoint URL" → set to https://bot.oneprompt.bot/webhooks/discord
        and subscribe to APPLICATION_AUTHORIZED. The portal sends a PING (type=0)
        to validate the URL, which we ack with 204.

        Docs: https://discord.com/developers/docs/events/webhook-events
        """
        if not cfg.discord_public_key:
            log.error("Discord webhook hit but DISCORD_PUBLIC_KEY is unset")
            return web.json_response({"error": "not configured"}, status=503)

        signature = request.headers.get("X-Signature-Ed25519")
        timestamp = request.headers.get("X-Signature-Timestamp")
        raw = await request.read()

        if not signature or not timestamp:
            return web.json_response({"error": "missing signature"}, status=401)

        try:
            verify_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(cfg.discord_public_key))
            verify_key.verify(bytes.fromhex(signature), timestamp.encode() + raw)
        except (InvalidSignature, ValueError):
            return web.json_response({"error": "bad signature"}, status=401)

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        ptype = payload.get("type")

        # PING — URL validation handshake.
        if ptype == 0:
            log.info("Discord webhook PING received — responding 204")
            return web.Response(status=204)

        # Event.
        if ptype == 1:
            event = payload.get("event") or {}
            etype = event.get("type")
            data = event.get("data") or {}
            if etype == "APPLICATION_AUTHORIZED":
                await handle_install(bot, data)
            else:
                log.info("Ignoring Discord webhook event type=%s", etype)
            return web.Response(status=204)

        log.info("Unknown Discord webhook payload type=%s", ptype)
        return web.Response(status=204)

    app.router.add_get("/health", health)
    app.router.add_post("/webhooks/polar", polar_webhook)
    app.router.add_post("/webhooks/video-ready", video_ready)
    app.router.add_post("/webhooks/discord", discord_webhook)

    if cfg.whop_api_key:
        from webhooks import whop_app
        whop_app.mount(app)

    return app


async def _dm(bot: discord.Client, discord_id: int, embed: discord.Embed) -> None:
    try:
        user = await bot.fetch_user(discord_id)
        await user.send(embed=embed)
    except discord.Forbidden:
        log.warning("Cannot DM user %s (DMs disabled)", discord_id)
    except Exception:
        log.exception("DM failed for %s", discord_id)
