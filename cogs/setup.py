"""/setmodal — collect Modal credentials, encrypt, deploy main.py."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from database import models as db
from services import embeds
from services.encryption import encrypt
from services.modal_deploy import deploy_modal_app

log = logging.getLogger(__name__)


class ModalCredsForm(discord.ui.Modal, title="Connect your Modal account"):
    token_id = discord.ui.TextInput(
        label="Modal Token ID",
        placeholder="ak-...",
        required=True,
        max_length=128,
    )
    token_secret = discord.ui.TextInput(
        label="Modal Token Secret",
        placeholder="as-...",
        required=True,
        max_length=128,
    )

    async def on_submit(self, interaction: discord.Interaction):
        tid = str(self.token_id.value).strip()
        tsec = str(self.token_secret.value).strip()

        await db.save_modal_credentials(
            interaction.user.id,
            encrypt(tid),
            encrypt(tsec),
        )

        await interaction.response.send_message(
            embed=embeds.processing(
                "Deploying your video generator…",
                "Connecting to Modal and building your app image. **First deploy takes "
                "3–5 minutes** (Modal has to build the CUDA image once). I'll DM you here "
                "when it's ready — feel free to close this.",
            ),
            ephemeral=True,
        )

        async def _deploy_bg():
            user = interaction.user
            try:
                url = await deploy_modal_app(tid, tsec)
                await db.set_modal_app_url(user.id, url)
                e = embeds.success(
                    "Your video generator is ready",
                    "Use `/create` to generate your first video.",
                )
            except Exception as ex:
                log.exception("modal deploy failed for user %s", user.id)
                e = embeds.error(
                    "Deployment failed",
                    f"Please double-check your Modal credentials and try `/setmodal` again.\n"
                    f"\n`{str(ex)[:300]}`",
                )
            try:
                await user.send(embed=e)
            except discord.Forbidden:
                log.warning("Cannot DM user %s (DMs disabled)", user.id)

        asyncio.create_task(_deploy_bg())


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setmodal", description="Connect your Modal.com account (Studio tier)")
    async def setmodal(self, interaction: discord.Interaction):
        user = await db.get_user(interaction.user.id)
        if not user or user["subscription_status"] != "pro":
            await interaction.response.send_message(
                embed=embeds.error(
                    "Subscription required",
                    "You need an active subscription first. Use `/subscribe` to get started.",
                ),
                ephemeral=True,
            )
            return
        if (user.get("tier") or "free") != "studio":
            await interaction.response.send_message(
                embed=embeds.info(
                    "You're on Pro (hosted)",
                    "`/setmodal` is for the **Studio** tier — bring your own Modal "
                    "account for unlimited videos. You're currently on **Pro** "
                    "(hosted Modal, monthly quota), no setup needed. Use `/create` "
                    "to generate; or upgrade to Studio with `/subscribe`.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(ModalCredsForm())


async def setup(bot: commands.Bot):
    await bot.add_cog(SetupCog(bot))
