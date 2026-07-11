"""/support, /status, /balance, /videos"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import cfg
from database import models as db
from services import embeds
from styles.presets import get_style_label, get_voice_label

log = logging.getLogger(__name__)


class SupportForm(discord.ui.Modal, title="Contact support"):
    message = discord.ui.TextInput(
        label="Your Message",
        placeholder="Describe your issue...",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_row = await db.get_user(interaction.user.id)
        sub_status = user_row["subscription_status"] if user_row else "free"

        try:
            owner = await interaction.client.fetch_user(cfg.support_user_id)
            await owner.send(
                embed=embeds.info(
                    f"Support — {interaction.user.name} ({interaction.user.id})",
                    f"**Status:** `{sub_status}`\n\n**Message:**\n{self.message.value}",
                )
            )
        except Exception:
            log.exception("Failed to forward support DM")

        await interaction.response.send_message(
            embed=embeds.success("Message sent", "We'll get back to you as soon as possible."),
            ephemeral=True,
        )


class SupportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="support", description="Contact OnePrompt support")
    async def support(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SupportForm())

    @app_commands.command(name="status", description="Your subscription and current job")
    async def status(self, interaction: discord.Interaction):
        user = await db.get_user(interaction.user.id)
        creds = await db.get_modal_credentials(interaction.user.id)
        count = await db.count_videos(interaction.user.id) if user else 0
        current = await db.current_processing_job(interaction.user.id) if user else None
        prefs = await db.get_user_preferences(interaction.user.id) if user else None

        sub = (user or {}).get("subscription_status", "free")
        tier = (user or {}).get("tier") or "free"
        sub_since = (user or {}).get("subscribed_at")
        sub_line = f"**Plan:** `{tier}`" + (f" (status: `{sub}`)" if tier != "free" else "")
        if sub_since:
            sub_line += f" — since <t:{int(sub_since.timestamp())}:D>"

        # Quota / usage line varies by tier (render-minutes for paid tiers).
        from config import cfg
        if tier in ("pro", "studio"):
            used_period = int((user or {}).get("minutes_used_period", 0) or 0)
            reset_at = (user or {}).get("quota_reset_at")
            is_trialing = bool((user or {}).get("is_trialing"))
            if tier == "studio":
                cap, label = cfg.studio_monthly_minutes, "This period"
            elif is_trialing:
                cap, label = cfg.trial_minutes, "Trial"
            else:
                cap, label = cfg.pro_monthly_minutes, "This period"
            quota_line = f"**{label}:** {used_period}/{cap} render-minutes"
            if reset_at and not is_trialing:
                quota_line += f" — resets <t:{int(reset_at.timestamp())}:R>"
        else:
            free_used = int((user or {}).get("free_attempts_used", 0) or 0)
            quota_line = f"**Free previews:** {free_used}/{cfg.free_generations} used"

        # Modal connection only matters for Enterprise BYO deployments.
        if tier == "studio" and cfg.enable_byo:
            if creds and creds.get("modal_app_url"):
                modal_line = "**Modal connected:** ✅ Yes"
            else:
                modal_line = "**Modal connected:** ❌ No (use `/setmodal`)"
        else:
            modal_line = ""

        job_line = "**Current job:** " + ("⏳ Processing" if current else "None")

        if prefs:
            style_line = f"**Style:** {get_style_label(prefs.get('style_preset_key'), prefs.get('style_custom'))}"
            voice_line = f"**Voice:** {get_voice_label(prefs.get('voice'))}"
        else:
            style_line = ""
            voice_line = ""

        body = "\n".join(
            line for line in [sub_line, quota_line, modal_line, style_line, voice_line,
                              f"**Videos generated:** {count}", job_line]
            if line
        )
        e = embeds.info("Your status", body)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="balance", description="Your Modal account balance & usage")
    async def balance(self, interaction: discord.Interaction):
        creds = await db.get_modal_credentials(interaction.user.id)
        if not creds:
            await interaction.response.send_message(
                embed=embeds.error("Modal not connected", "Use `/setmodal` first."),
                ephemeral=True,
            )
            return

        # Modal doesn't expose a public balance API. Deep-link to the dashboard
        # — always up-to-date, no auth dance, shows the full picture (balance,
        # auto-recharge settings, invoices, usage breakdown by app).
        body = (
            "Modal doesn't expose balance over their API, so I can't show it here directly.\n\n"
            "**[→ Open your Modal billing dashboard](https://modal.com/settings/billing)**\n\n"
            "There you'll see your current balance, month-to-date usage, "
            "and can top up your account."
        )
        await interaction.response.send_message(
            embed=embeds.info("Modal billing", body),
            ephemeral=True,
        )

    @app_commands.command(name="videos", description="Your last 5 generated videos")
    async def videos(self, interaction: discord.Interaction):
        rows = await db.list_recent_videos(interaction.user.id, limit=5)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info(
                    "No videos yet",
                    "Use `/create` to generate your first one.",
                ),
                ephemeral=True,
            )
            return

        lines = []
        for r in rows:
            ts = int(r["completed_at"].timestamp()) if r["completed_at"] else None
            ts_part = f" — <t:{ts}:R>" if ts else ""
            url = r.get("video_url") or "(missing url)"
            prompt = (r["prompt"] or "")[:80]
            lines.append(f"• [{prompt}]({url}){ts_part}")

        await interaction.response.send_message(
            embed=embeds.info("Your recent videos", "\n".join(lines)),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SupportCog(bot))
