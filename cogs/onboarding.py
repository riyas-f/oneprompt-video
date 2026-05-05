"""/start, /subscribe, /help"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import cfg
from database import models as db
from services import embeds
from services import polar


def build_start_message(username: str | None = None, *, returning: bool = False) -> discord.Embed:
    """Welcome embed shown by /start and on APPLICATION_AUTHORIZED install."""
    if returning:
        title = f"Welcome back{f', {username}' if username else ''}"
        intro = "Good to see you again — OnePrompt is ready when you are."
    else:
        title = f"Welcome to OnePrompt{f', {username}' if username else ''}"
        intro = "Generate faceless AI videos from a single prompt — straight from Discord."
    return embeds.welcome(
        title,
        (
            f"{intro}\n\n"
            f"**${cfg.price_usd}/month, unlimited videos. {cfg.polar_trial_days}-day free trial — no charge today.**\n\n"
            f"**Next step:** `/subscribe` to start your free trial.\n"
            f"Then `/setmodal` to connect your Modal account, and `/create` to make a video.\n"
            f"Run `/help` to see every command."
        ),
    )


class Onboarding(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="start", description="Get started with OnePrompt")
    async def start(self, interaction: discord.Interaction):
        existing = await db.get_user(interaction.user.id)
        await db.upsert_user(interaction.user.id, interaction.user.name)
        e = build_start_message(interaction.user.name, returning=existing is not None)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="subscribe", description="Subscribe to OnePrompt Pro")
    async def subscribe(self, interaction: discord.Interaction):
        await db.upsert_user(interaction.user.id, interaction.user.name)
        user = await db.get_user(interaction.user.id)

        if user and user["subscription_status"] == "pro":
            e = embeds.success(
                "You're already subscribed",
                f"Subscribed since: <t:{int(user['subscribed_at'].timestamp())}:D>\n"
                f"Use `/setmodal` if you haven't connected your Modal account yet, "
                f"or `/create` to generate a video.",
            )
            await interaction.response.send_message(embed=e, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            url, checkout_id = await polar.create_checkout_session(
                interaction.user.id, interaction.user.name
            )
            await db.record_pending_checkout(checkout_id, interaction.user.id)
        except Exception as ex:
            await interaction.followup.send(
                embed=embeds.error("Could not start checkout", f"Please try again or use `/support`.\n`{ex}`"),
                ephemeral=True,
            )
            return

        e = embeds.info(
            f"Start your {cfg.polar_trial_days}-day free trial",
            f"Then **${cfg.price_usd}/month**. Cancel anytime during the trial — no charge.\n\n"
            "Click below to add a card. You'll get a confirmation here as soon as it's done.",
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=f"Start free trial", url=url, style=discord.ButtonStyle.link))
        await interaction.followup.send(embed=e, view=view, ephemeral=True)

    @app_commands.command(name="help", description="List all OnePrompt commands")
    async def help_cmd(self, interaction: discord.Interaction):
        e = embeds.info(
            "OnePrompt — Commands",
            (
                "`/start` — quick intro\n"
                f"`/subscribe` — start your {cfg.polar_trial_days}-day free trial (then ${cfg.price_usd}/mo)\n"
                "`/setmodal` — connect your Modal.com account\n"
                "`/style` — pick your video art style (8 presets + custom)\n"
                "`/voice` — pick your narrator voice\n"
                "`/create` — generate a new video\n"
                "`/status` — your subscription, style, voice + current job\n"
                "`/balance` — Modal account billing\n"
                "`/videos` — your last 5 videos\n"
                "`/support` — contact us\n\n"
                "*All interactions happen here in DMs — your generations are private.*"
            ),
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Onboarding(bot))
