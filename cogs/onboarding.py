"""/start, /subscribe, /help"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import cfg
from database import models as db
from services import embeds
from services import suby


class Onboarding(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="start", description="Get started with OnePrompt")
    async def start(self, interaction: discord.Interaction):
        await db.upsert_user(interaction.user.id, interaction.user.name)
        e = embeds.welcome(
            "Welcome to OnePrompt",
            (
                f"Generate faceless AI videos from a single prompt — straight from Discord.\n\n"
                f"**${cfg.price_usd}/month, unlimited videos.**\n\n"
                f"**Next step:** `/subscribe` to start your subscription."
            ),
        )
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
            url, payment_id = await suby.create_checkout_session(
                interaction.user.id, interaction.user.name
            )
            await db.record_pending_checkout(payment_id, interaction.user.id)
        except Exception as ex:
            await interaction.followup.send(
                embed=embeds.error("Could not start checkout", f"Please try again or use `/support`.\n`{ex}`"),
                ephemeral=True,
            )
            return

        e = embeds.info(
            "Subscribe — $29/month",
            "Click below to subscribe. Once payment completes you'll get a confirmation here automatically.",
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Subscribe", url=url, style=discord.ButtonStyle.link))
        await interaction.followup.send(embed=e, view=view, ephemeral=True)

    @app_commands.command(name="help", description="List all OnePrompt commands")
    async def help_cmd(self, interaction: discord.Interaction):
        e = embeds.info(
            "OnePrompt — Commands",
            (
                "`/start` — quick intro\n"
                "`/subscribe` — start your $29/month subscription\n"
                "`/setmodal` — connect your Modal.com account\n"
                "`/create` — generate a new video\n"
                "`/status` — your subscription + current job\n"
                "`/balance` — Modal account balance & usage\n"
                "`/videos` — your last 5 videos\n"
                "`/support` — contact us\n\n"
                "*All interactions happen here in DMs — your generations are private.*"
            ),
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Onboarding(bot))
