"""/create — kick off a video generation job."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import cfg
from database import models as db
from services import embeds
from services.api_client import request_video
from styles.presets import get_style_object

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

    def __init__(self, modal_url: str, *, meter: str = "none", quota: int = 0, trialing: bool = False):
        """meter: 'none' (no counter), 'free' (lifetime free counter), 'period' (monthly period counter)."""
        super().__init__()
        self._modal_url = modal_url
        self._meter = meter
        self._quota = quota
        self._trialing = trialing

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

        prefs = await db.get_user_preferences(interaction.user.id)
        style_obj = get_style_object(prefs.get("style_preset_key"), prefs.get("style_custom"))

        try:
            resp = await request_video(
                content=topic,
                modal_url=self._modal_url,
                extra_context=extra,
                discord_id=interaction.user.id,
                job_id=job_id,
                style=style_obj,
                style_key=prefs.get("style_preset_key"),
                voice=prefs.get("voice") or "af_heart",
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

        # Only meter usage once dispatch has succeeded.
        free_note = ""
        if self._meter == "free":
            used = await db.increment_free_attempt(interaction.user.id)
            remaining = max(0, cfg.free_generations - used)
            free_note = (
                f"\n\n*Free preview {used}/{cfg.free_generations}. "
                f"{remaining} left — `/subscribe` for unlimited.*"
            )
        elif self._meter == "period":
            used = await db.increment_period_usage(interaction.user.id)
            label = "Trial" if self._trialing else "Plan"
            free_note = f"\n\n*{label}: {used}/{self._quota} videos this period.*"

        await interaction.response.send_message(
            embed=embeds.processing(
                "🎬 Generating your video",
                "I'll DM you when it's ready. This usually takes 5–10 minutes." + free_note,
            ),
            ephemeral=True,
        )


class VideoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="create", description="Generate a new video")
    async def create(self, interaction: discord.Interaction):
        await db.upsert_user(interaction.user.id, interaction.user.name)
        user = await db.get_user(interaction.user.id)
        tier = (user or {}).get("tier") or "free"
        is_pro = bool(user and user["subscription_status"] == "pro")
        free_used = int((user or {}).get("free_attempts_used", 0) or 0)

        is_trialing = bool((user or {}).get("is_trialing"))
        quota = 0
        trialing_flag = False

        if is_pro and tier == "studio":
            creds = await db.get_modal_credentials(interaction.user.id)
            if not creds or not creds.get("modal_app_url"):
                await interaction.response.send_message(
                    embed=embeds.error(
                        "Modal not connected",
                        "You're on **Studio** — please connect your Modal account "
                        "with `/setmodal` to start generating.",
                    ),
                    ephemeral=True,
                )
                return
            used = await db.get_or_reset_period_usage(interaction.user.id)
            if used >= cfg.studio_monthly_quota:
                reset = (user or {}).get("quota_reset_at")
                when = f" Resets <t:{int(reset.timestamp())}:R>." if reset else ""
                await interaction.response.send_message(
                    embed=embeds.error(
                        "Monthly quota reached",
                        f"You've used all **{cfg.studio_monthly_quota}** Studio videos "
                        f"for this period.{when}\n\nFor higher volume, contact `/support` "
                        "about Enterprise pricing.",
                    ),
                    ephemeral=True,
                )
                return
            modal_url = creds["modal_app_url"]
            meter = "period"
            quota = cfg.studio_monthly_quota

        elif is_pro and tier == "pro":
            # Hosted Pro: tighter cap during trial than the paid period.
            effective_quota = cfg.trial_quota if is_trialing else cfg.pro_monthly_quota
            used = await db.get_or_reset_period_usage(interaction.user.id)
            if used >= effective_quota:
                reset = (user or {}).get("quota_reset_at")
                when = f" Resets <t:{int(reset.timestamp())}:R>." if reset else ""
                if is_trialing:
                    msg = (
                        f"You've used all **{cfg.trial_quota}** trial videos. "
                        "Your trial converts automatically — once it does, you'll get "
                        f"**{cfg.pro_monthly_quota} videos / month**."
                    )
                else:
                    msg = (
                        f"You've used all **{cfg.pro_monthly_quota}** videos for this period.{when}\n\n"
                        "Need more? Upgrade to **Studio** ($79/mo) for "
                        f"{cfg.studio_monthly_quota} videos / month with your own Modal account."
                    )
                await interaction.response.send_message(
                    embed=embeds.error("Quota reached", msg),
                    ephemeral=True,
                )
                return
            # Global daily fuse — protects against runaway Modal spend.
            if await db.count_hosted_videos_today() >= cfg.max_hosted_videos_per_day:
                await interaction.response.send_message(
                    embed=embeds.error(
                        "Service busy",
                        "We've hit today's hosted-Modal capacity. Please try again "
                        "in a few hours, or upgrade to Studio for your own Modal compute.",
                    ),
                    ephemeral=True,
                )
                return
            modal_url = cfg.admin_modal_url
            meter = "period"
            quota = effective_quota
            trialing_flag = is_trialing

        elif free_used < cfg.free_generations:
            if await db.count_hosted_videos_today() >= cfg.max_hosted_videos_per_day:
                await interaction.response.send_message(
                    embed=embeds.error(
                        "Service busy",
                        "We've hit today's free-tier capacity. Please try again later.",
                    ),
                    ephemeral=True,
                )
                return
            modal_url = cfg.admin_modal_url
            meter = "free"

        else:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Free previews used",
                    f"You've used your {cfg.free_generations} free videos. "
                    f"Run **`/subscribe`** to pick a plan — Pro ${cfg.price_usd}/mo "
                    f"({cfg.pro_monthly_quota} videos/mo, hosted) or Studio $79/mo "
                    f"({cfg.studio_monthly_quota} videos/mo, BYO Modal).",
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

        await interaction.response.send_modal(
            CreateForm(modal_url=modal_url, meter=meter, quota=quota, trialing=trialing_flag)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VideoCog(bot))
