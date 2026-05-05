"""Single source of truth for environment configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


@dataclass(frozen=True)
class Config:
    discord_token: str
    discord_public_key: str
    database_url: str

    polar_access_token: str
    polar_webhook_secret: str
    polar_product_id: str
    polar_api_base: str
    polar_success_url: str
    polar_trial_days: int

    encryption_key: str

    modal_app_path: str
    modal_graphql_url: str

    video_api_url: str
    video_api_callback_secret: str
    bot_public_url: str

    support_user_id: int

    webhook_host: str
    webhook_port: int

    price_usd: int


def load() -> Config:
    return Config(
        discord_token=_req("DISCORD_BOT_TOKEN"),
        discord_public_key=os.getenv("DISCORD_PUBLIC_KEY", ""),
        database_url=_req("DATABASE_URL"),
        polar_access_token=_req("POLAR_ACCESS_TOKEN"),
        polar_webhook_secret=_req("POLAR_WEBHOOK_SECRET"),
        polar_product_id=_req("POLAR_PRODUCT_ID"),
        polar_api_base=os.getenv("POLAR_API_BASE", "https://api.polar.sh"),
        polar_success_url=os.getenv("POLAR_SUCCESS_URL", ""),
        polar_trial_days=int(os.getenv("POLAR_TRIAL_DAYS", "7")),
        encryption_key=_req("ENCRYPTION_KEY"),
        modal_app_path=os.getenv("MODAL_APP_PATH", "./modal_app/app.py"),
        modal_graphql_url=os.getenv("MODAL_GRAPHQL_URL", "https://api.modal.com/graphql"),
        video_api_url=_req("VIDEO_API_URL"),
        video_api_callback_secret=_req("VIDEO_API_CALLBACK_SECRET"),
        bot_public_url=_req("BOT_PUBLIC_URL"),
        support_user_id=int(_req("SUPPORT_DISCORD_USER_ID")),
        webhook_host=os.getenv("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
        price_usd=int(os.getenv("PRICE_USD", "29")),
    )


cfg = load()
