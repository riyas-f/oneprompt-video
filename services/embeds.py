"""Shared embed builders. Keeps visual style consistent across cogs."""
from __future__ import annotations

import discord

BRAND = 0xFF6B35
GREEN = 0x2ECC71
RED = 0xE74C3C
FOOTER = "OnePrompt — AI Video for Creators"


def _base(title: str, description: str, color: int) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER)
    return e


def info(title: str, description: str) -> discord.Embed:
    return _base(title, description, BRAND)


def success(title: str, description: str) -> discord.Embed:
    return _base(f"✅ {title}", description, GREEN)


def error(title: str, description: str) -> discord.Embed:
    return _base(f"❌ {title}", description, RED)


def processing(title: str, description: str) -> discord.Embed:
    return _base(f"⏳ {title}", description, BRAND)


def welcome(title: str, description: str) -> discord.Embed:
    return _base(f"👋 {title}", description, BRAND)
