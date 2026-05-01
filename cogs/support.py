"""/support, /status, /balance, /videos"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import cfg
from database import models as db
from services import embeds
from services.encryption import decrypt
from services.modal_billing import fetch_balance

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

        sub = (user or {}).get("subscription_status", "free")
        sub_since = (user or {}).get("subscribed_at")
        sub_line = f"**Subscription:** `{sub}`"
        if sub_since:
            sub_line += f" — since <t:{int(sub_since.timestamp())}:D>"

        modal_line = "**Modal connected:** "
        if creds and creds.get("modal_app_url"):
            modal_line += "✅ Yes"
        else:
            modal_line += "❌ No (use `/setmodal`)"

        job_line = "**Current job:** " + ("⏳ Processing" if current else "None")

        e = embeds.info(
            "Your status",
            f"{sub_line}\n{modal_line}\n**Videos generated:** {count}\n{job_line}",
        )
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

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            tid = decrypt(creds["token_id_encrypted"])
            tsec = decrypt(creds["token_secret_encrypted"])
            info = await fetch_balance(tid, tsec)
        except Exception as ex:
            await interaction.followup.send(
                embed=embeds.error(
                    "Could not fetch balance",
                    f"Modal API error: `{str(ex)[:200]}`",
                ),
                ephemeral=True,
            )
            return

        balance = info.get("balance_usd")
        usage = info.get("mtd_usage_usd")
        ws = info.get("workspace") or "—"
        body = (
            f"**Workspace:** {ws}\n"
            f"**Balance:** {f'${balance:.2f}' if isinstance(balance, (int, float)) else 'unknown'}\n"
            f"**Month-to-date usage:** {f'${usage:.2f}' if isinstance(usage, (int, float)) else 'unknown'}\n\n"
            f"Top up at https://modal.com/settings/billing"
        )
        await interaction.followup.send(embed=embeds.info("Modal account", body), ephemeral=True)

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
