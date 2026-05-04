"""Listen for Discord's APPLICATION_AUTHORIZED event and DM the user.

Two delivery paths feed the same handler (`handle_install`):
  - Gateway: discord.py 2.3 doesn't surface APPLICATION_AUTHORIZED, so we hook
    the low-level parser table on the connection state.
  - HTTP: Discord's "Webhook Events URL" feature posts the event to
    `/webhooks/discord` (handled in webhooks/server.py) — required because the
    gateway does not always dispatch APPLICATION_AUTHORIZED for User Apps.

Docs:
  https://discord.com/developers/docs/events/gateway-events#application-authorized
  https://discord.com/developers/docs/events/webhook-events
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from cogs.onboarding import build_start_message
from database import models as db

log = logging.getLogger("oneprompt.install")

USER_INSTALL = 1


async def handle_install(bot: discord.Client, data: dict) -> None:
    """Process an APPLICATION_AUTHORIZED payload (from gateway or webhook).

    `data` is the inner event data — the dict containing integration_type,
    user, scopes, (optional) guild.
    """
    try:
        integration_type = data.get("integration_type")
        if integration_type != USER_INSTALL:
            log.info("Ignoring APPLICATION_AUTHORIZED integration_type=%s", integration_type)
            return

        user_data = data.get("user") or {}
        try:
            user_id = int(user_data["id"])
        except (KeyError, TypeError, ValueError):
            log.warning("APPLICATION_AUTHORIZED missing user.id: %s", data)
            return
        username = user_data.get("username") or ""
        scopes = data.get("scopes") or []

        existing = await db.get_user(user_id)
        returning = existing is not None
        await db.upsert_user(user_id, username)

        log.info(
            "User install: id=%s username=%s returning=%s scopes=%s",
            user_id, username, returning, scopes,
        )

        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            dm = user.dm_channel or await user.create_dm()
            await dm.send(embed=build_start_message(username, returning=returning))
            log.info("Sent install DM to %s (%s)", username, user_id)
        except discord.Forbidden:
            log.warning("Cannot DM user %s — DMs disabled or blocked", user_id)
        except discord.HTTPException as ex:
            log.warning("HTTP error DMing user %s: %s", user_id, ex)
    except Exception:
        log.exception("APPLICATION_AUTHORIZED handler crashed")


class InstallListener(commands.Cog):
    """Hook the gateway parser so we still get the event if Discord sends it there."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._install_parser()

    def _install_parser(self) -> None:
        parsers = self.bot._connection.parsers  # type: ignore[attr-defined]
        prev = parsers.get("APPLICATION_AUTHORIZED")

        def parse_application_authorized(data: dict) -> None:
            if prev is not None:
                try:
                    prev(data)
                except Exception:
                    log.exception("Upstream APPLICATION_AUTHORIZED parser raised")
            asyncio.create_task(handle_install(self.bot, data))

        parsers["APPLICATION_AUTHORIZED"] = parse_application_authorized
        log.info("APPLICATION_AUTHORIZED parser hook installed")


async def setup(bot: commands.Bot):
    await bot.add_cog(InstallListener(bot))
