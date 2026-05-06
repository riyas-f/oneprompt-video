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
    trial_blurb = (
        f" ({cfg.polar_trial_days}-day free trial, {cfg.trial_quota} videos)"
        if cfg.polar_trial_days > 0 else ""
    )
    return embeds.welcome(
        title,
        (
            f"{intro}\n\n"
            f"**Try `/create` right now — {cfg.free_generations} free videos, no card, no setup.**\n\n"
            f"After that, pick a plan with `/subscribe`:\n"
            f"• **Pro ${cfg.price_usd}/mo** — {cfg.pro_monthly_quota} videos/mo, hosted{trial_blurb}.\n"
            f"• **Studio $79/mo** — {cfg.studio_monthly_quota} videos/mo, bring your own Modal account.\n\n"
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

    @app_commands.command(name="subscribe", description="Pick a plan: Pro (hosted) or Studio (BYO Modal)")
    async def subscribe(self, interaction: discord.Interaction):
        await db.upsert_user(interaction.user.id, interaction.user.name)
        user = await db.get_user(interaction.user.id)

        if user and user["subscription_status"] == "pro":
            tier = (user.get("tier") or "pro").capitalize()
            e = embeds.success(
                f"You're already on {tier}",
                f"Subscribed since: <t:{int(user['subscribed_at'].timestamp())}:D>\n"
                f"Use `/create` to generate a video, `/status` to check your quota, "
                f"or `/setmodal` (Studio only).",
            )
            await interaction.response.send_message(embed=e, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            pro_url, pro_id = await polar.create_checkout_session(
                interaction.user.id, interaction.user.name, cfg.polar_product_id_pro,
            )
            studio_url, studio_id = await polar.create_checkout_session(
                interaction.user.id, interaction.user.name, cfg.polar_product_id_studio,
            )
            await db.record_pending_checkout(pro_id, interaction.user.id)
            await db.record_pending_checkout(studio_id, interaction.user.id)
        except Exception as ex:
            await interaction.followup.send(
                embed=embeds.error("Could not start checkout", f"Please try again or use `/support`.\n`{ex}`"),
                ephemeral=True,
            )
            return

        pro_subline = (
            f"  ↳ {cfg.polar_trial_days}-day free trial ({cfg.trial_quota} videos). Zero setup."
            if cfg.polar_trial_days > 0
            else "  ↳ Zero setup. Cancel anytime."
        )
        e = embeds.info(
            "Pick your plan",
            f"**Pro — ${cfg.price_usd}/mo** · {cfg.pro_monthly_quota} videos / month · hosted\n"
            f"{pro_subline}\n\n"
            f"**Studio — $79/mo** · {cfg.studio_monthly_quota} videos / month · bring your own Modal\n"
            "  ↳ For agencies. One-time `/setmodal` after upgrade — you keep control of your compute.",
        )
        pro_button_label = (
            f"Start Pro trial — ${cfg.price_usd}/mo"
            if cfg.polar_trial_days > 0
            else f"Get Pro — ${cfg.price_usd}/mo"
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label=pro_button_label,
            url=pro_url,
            style=discord.ButtonStyle.link,
        ))
        view.add_item(discord.ui.Button(
            label="Get Studio — $79/mo",
            url=studio_url,
            style=discord.ButtonStyle.link,
        ))
        await interaction.followup.send(embed=e, view=view, ephemeral=True)

    @app_commands.command(name="help", description="List all OnePrompt commands")
    async def help_cmd(self, interaction: discord.Interaction):
        e = embeds.info(
            "OnePrompt — Commands",
            (
                "`/start` — quick intro\n"
                f"`/create` — generate a video ({cfg.free_generations} free, then `/subscribe`)\n"
                f"`/subscribe` — Pro ${cfg.price_usd}/mo ({cfg.pro_monthly_quota} videos) or Studio $79/mo ({cfg.studio_monthly_quota} videos, BYO Modal)\n"
                "`/setmodal` — connect your Modal.com account (Studio only)\n"
                "`/style` — pick your video art style (8 presets + custom)\n"
                "`/voice` — pick your narrator voice\n"
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
