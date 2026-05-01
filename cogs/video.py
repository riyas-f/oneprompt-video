"""/create — kick off a video generation job."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from database import models as db
from services import embeds
from services.api_client import request_video

log = logging.getLogger(__name__)


class CreateForm(discord.ui.Modal, title="Generate a video"):
    topic = discord.ui.TextInput(
        label="Video Topic",
        placeholder="e.g. 5 mysteries science can't explain",
        required=True,
        max_length=200,
    )
    extra = discord.ui.TextInput(
        label="Extra Details (optional)",
        placeholder="e.g. Keep it under 60 seconds, friendly tone, suitable for teens",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, modal_url: str):
        super().__init__()
        self._modal_url = modal_url

    async def on_submit(self, interaction: discord.Interaction):
        topic = str(self.topic.value).strip()
        extra = str(self.extra.value or "").strip()

        # Race-safe re-check: someone could have started a job between command
        # invocation and form submission.
        if await db.has_processing_job(interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.processing(
                    "You already have a video generating",
                    "Please wait for it to finish. I'll DM you when it's done.",
                ),
                ephemeral=True,
            )
            return

        job_id = await db.create_job(interaction.user.id, topic, extra or None)

        try:
            resp = await request_video(
                content=topic,
                modal_url=self._modal_url,
                extra_context=extra,
                discord_id=interaction.user.id,
                job_id=job_id,
            )
            api_job_id = resp.get("api_job_id")
            if api_job_id:
                await db.set_job_api_id(job_id, str(api_job_id))
        except Exception as ex:
            log.exception("video API call failed")
            await db.fail_job(job_id, f"API call failed: {ex}")
            await interaction.response.send_message(
                embed=embeds.error(
                    "Could not start generation",
                    f"Please try again or use `/support` if it persists.\n`{str(ex)[:200]}`",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embeds.processing(
                "🎬 Generating your video",
                "I'll DM you when it's ready. This usually takes 5–10 minutes.",
            ),
            ephemeral=True,
        )


class VideoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="create", description="Generate a new video")
    async def create(self, interaction: discord.Interaction):
        user = await db.get_user(interaction.user.id)
        if not user or user["subscription_status"] != "pro":
            await interaction.response.send_message(
                embed=embeds.error(
                    "Subscription required",
                    "You need an active subscription. Use `/subscribe` to get started.",
                ),
                ephemeral=True,
            )
            return

        creds = await db.get_modal_credentials(interaction.user.id)
        if not creds or not creds.get("modal_app_url"):
            await interaction.response.send_message(
                embed=embeds.error(
                    "Modal not connected",
                    "Please set up your Modal account first using `/setmodal`.",
                ),
                ephemeral=True,
            )
            return

        if await db.has_processing_job(interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.processing(
                    "You already have a video generating",
                    "Please wait for it to finish. I'll DM you when it's done.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CreateForm(modal_url=creds["modal_app_url"]))


async def setup(bot: commands.Bot):
    await bot.add_cog(VideoCog(bot))
