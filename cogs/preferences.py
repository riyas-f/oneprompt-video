"""/style and /voice — user preference pickers using ephemeral select menus."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import cfg
from database import models as db
from services import embeds
from styles.presets import (
    STYLE_PRESETS,
    VOICE_PRESETS,
    CUSTOM_STYLE_FIELDS,
    custom_style_from_form,
)

log = logging.getLogger(__name__)

CUSTOM_STYLE_VALUE = "__custom__"


class StyleSelect(discord.ui.Select):
    def __init__(self):
        options: list[discord.SelectOption] = []
        for key, preset in STYLE_PRESETS.items():
            options.append(
                discord.SelectOption(
                    label=preset["label"],
                    value=key,
                    description=preset["tagline"][:100],
                    emoji=preset["emoji"],
                )
            )
        options.append(
            discord.SelectOption(
                label="Custom…",
                value=CUSTOM_STYLE_VALUE,
                description="Define your own style — opens a form",
                emoji="✨",
            )
        )
        super().__init__(
            placeholder="Pick a style for your videos…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == CUSTOM_STYLE_VALUE:
            await interaction.response.send_modal(CustomStyleForm())
            return

        await db.set_style_preset(interaction.user.id, choice)
        preset = STYLE_PRESETS[choice]
        await interaction.response.edit_message(
            embed=embeds.success(
                "Style updated",
                f"Your videos will now use **{preset['emoji']} {preset['label']}**.\n"
                f"_{preset['tagline']}_\n\n"
                f"Best for: {preset['best_for']}",
            ),
            view=None,
        )


class StyleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(StyleSelect())


class CustomStyleForm(discord.ui.Modal, title="Custom video style"):
    def __init__(self):
        super().__init__()
        self._inputs: dict[str, discord.ui.TextInput] = {}
        for key, label, placeholder in CUSTOM_STYLE_FIELDS:
            ti = discord.ui.TextInput(
                label=label,
                placeholder=placeholder,
                required=True,
                style=discord.TextStyle.paragraph,
                max_length=300,
            )
            self.add_item(ti)
            self._inputs[key] = ti

    async def on_submit(self, interaction: discord.Interaction):
        values = {k: str(ti.value) for k, ti in self._inputs.items()}
        custom = custom_style_from_form(values)
        await db.set_style_custom(interaction.user.id, custom)
        await interaction.response.send_message(
            embed=embeds.success(
                "Custom style saved",
                "Your videos will now use your custom style. Run `/create` to try it out.",
            ),
            ephemeral=True,
        )


class VoiceSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=v["label"],
                value=key,
                description=v["tagline"][:100],
                emoji=v["emoji"],
            )
            for key, v in VOICE_PRESETS.items()
        ]
        super().__init__(
            placeholder="Pick a narrator voice…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        await db.set_voice(interaction.user.id, choice)
        v = VOICE_PRESETS[choice]
        body = (
            f"Your videos will now be narrated by **{v['emoji']} {v['label']}**.\n"
            f"_{v['tagline']}_"
        )
        if cfg.r2_public_base_url:
            sample = f"{cfg.r2_public_base_url.rstrip('/')}/audio-samples/{choice}.wav"
            body += f"\n\n[🔊 Hear a sample]({sample})"
        await interaction.response.edit_message(
            embed=embeds.success("Voice updated", body),
            view=None,
        )


class VoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(VoiceSelect())


class PreferencesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="style", description="Pick your video art style")
    async def style(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=embeds.info(
                "Pick a style",
                "This style applies to every video you generate. "
                "You can change it any time with `/style`.",
            ),
            view=StyleView(),
            ephemeral=True,
        )

    @app_commands.command(name="voice", description="Pick your narrator voice")
    async def voice(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=embeds.info(
                "Pick a voice",
                "This voice narrates every video you generate. "
                "You can change it any time with `/voice`.",
            ),
            view=VoiceView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PreferencesCog(bot))
