"""OnePrompt Discord Bot — entry point.

Runs the discord.py client and the aiohttp webhook server in the same event loop.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from aiohttp import web
from discord.ext import commands

from config import cfg
from database.connection import init_pool, close_pool
from webhooks.server import build_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("oneprompt")


intents = discord.Intents.default()
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("Logged in as %s (id=%s); guilds=%d", bot.user, bot.user.id, len(bot.guilds))
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d slash commands globally", len(synced))
    except Exception:
        log.exception("Slash command sync failed")


# DM-only enforcement for every slash command.
async def _dm_only(interaction: discord.Interaction) -> bool:
    if interaction.guild is not None:
        try:
            await interaction.response.send_message(
                "Please use this command in our DMs for privacy.",
                ephemeral=True,
            )
        except Exception:
            pass
        return False
    return True


bot.tree.interaction_check = _dm_only  # type: ignore[assignment]


async def _load_cogs():
    for ext in ("cogs.onboarding", "cogs.setup", "cogs.video", "cogs.support", "cogs.preferences", "cogs.install_listener"):
        await bot.load_extension(ext)
        log.info("Loaded extension %s", ext)


async def _run_web():
    app = build_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.webhook_host, cfg.webhook_port)
    await site.start()
    log.info("Webhook server listening on %s:%d", cfg.webhook_host, cfg.webhook_port)
    # Keep alive forever.
    await asyncio.Event().wait()


async def main():
    await init_pool()
    await _load_cogs()
    try:
        await asyncio.gather(
            bot.start(cfg.discord_token),
            _run_web(),
        )
    finally:
        await close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
