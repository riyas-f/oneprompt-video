"""aiohttp server for Suby + API-server callbacks. Runs in the bot's event loop."""
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
from services.suby import verify_webhook_signature, compute_signature

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

    async def suby_webhook(request: web.Request) -> web.Response:
        raw = await request.read()
        sig = request.headers.get("X-Webhook-Signature") or request.headers.get("Suby-Signature")
        ts = request.headers.get("X-Webhook-Timestamp")

        if not verify_webhook_signature(raw, sig, ts):
            log.warning(
                "Suby webhook signature mismatch. expected=%s received=%s ts=%s body[:200]=%r",
                compute_signature(raw, ts), sig, ts, raw[:200],
            )
            return web.json_response({"error": "bad signature"}, status=401)

        try:
            event = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        etype = (
            request.headers.get("X-Webhook-Event")
            or event.get("type")
            or ""
        ).upper()

        # One-shot diagnostic — remove once we've confirmed Suby's payload shape.
        log.info("Suby webhook FULL BODY (event=%s): %s", etype, raw.decode("utf-8", "replace")[:2000])

        data = event.get("data") or {}
        payment = data.get("payment") or data
        subscription = data.get("subscription") or {}
        context = data.get("context") or {}
        meta = (
            context.get("metadata")
            or payment.get("metadata")
            or data.get("metadata")
            or {}
        )
        payment_id = payment.get("id") or data.get("paymentId")

        # Primary lookup: paymentId we recorded at /subscribe time.
        discord_id: int | None = None
        if payment_id:
            discord_id = await db.lookup_discord_id_by_payment(str(payment_id))

        # Fallbacks for events that aren't tied to one of our paymentIds
        # (e.g. dashboard test events, or future event types).
        if discord_id is None:
            ext = (
                context.get("externalRef")
                or payment.get("externalRef")
                or data.get("externalRef")
                or meta.get("discord_id")
            )
            if ext:
                try:
                    discord_id = int(ext)
                except ValueError:
                    discord_id = None

        if discord_id is None:
            log.info(
                "Suby %s ignored — no matching paymentId/externalRef (likely dashboard test)",
                etype,
            )
            return web.json_response({"ok": True})

        if etype in ("PAYMENT_SUCCESS", "SUBSCRIPTION_ACTIVATED", "SUBSCRIPTION_RENEWED"):
            customer_id = (
                payment.get("customerId")
                or (payment.get("customer") or {}).get("id")
                or data.get("customerId")
                or ""
            )
            sub_id = (
                subscription.get("id")
                or payment.get("subscriptionId")
                or data.get("subscriptionId")
                or payment.get("id")
                or ""
            )
            user_row = await db.get_user(discord_id)
            already_pro = bool(user_row and user_row["subscription_status"] == "pro")
            await db.upsert_user(discord_id, meta.get("discord_username", "user"))
            await db.mark_subscribed(discord_id, str(customer_id), str(sub_id))
            if payment_id:
                await db.delete_pending_checkout(str(payment_id))
            if not already_pro:
                await _dm(
                    bot, discord_id,
                    embeds.success(
                        "You're subscribed to OnePrompt Pro",
                        "**Next step:** run `/setmodal` to connect your Modal.com account, "
                        "then `/create` to generate your first video.",
                    ),
                )

        elif etype in ("SUBSCRIPTION_CANCELLED", "SUBSCRIPTION_EXPIRED"):
            await db.mark_cancelled(discord_id)
            await _dm(
                bot, discord_id,
                embeds.info(
                    "Subscription cancelled",
                    "Your OnePrompt subscription has ended. Use `/subscribe` anytime to reactivate.",
                ),
            )

        elif etype == "PAYMENT_FAILED":
            await _dm(
                bot, discord_id,
                embeds.error(
                    "Payment failed",
                    "Your latest charge didn't go through. Please update your card or try `/subscribe` again.",
                ),
            )

        else:
            log.info("Ignoring Suby event: %s", etype)

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
    app.router.add_post("/webhooks/suby", suby_webhook)
    app.router.add_post("/webhooks/video-ready", video_ready)
    app.router.add_post("/webhooks/discord", discord_webhook)
    return app


async def _dm(bot: discord.Client, discord_id: int, embed: discord.Embed) -> None:
    try:
        user = await bot.fetch_user(discord_id)
        await user.send(embed=embed)
    except discord.Forbidden:
        log.warning("Cannot DM user %s (DMs disabled)", discord_id)
    except Exception:
        log.exception("DM failed for %s", discord_id)
