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
    database_url: str

    suby_api_key: str
    suby_webhook_secret: str
    suby_product_id: str
    suby_api_base: str
    suby_success_url: str
    suby_cancel_url: str
    suby_price_cents: int
    suby_currency: str

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
        database_url=_req("DATABASE_URL"),
        suby_api_key=_req("SUBY_API_KEY"),
        suby_webhook_secret=_req("SUBY_WEBHOOK_SECRET"),
        suby_product_id=_req("SUBY_PRODUCT_ID"),
        suby_api_base=os.getenv("SUBY_API_BASE", "https://api.suby.fi"),
        suby_success_url=os.getenv("SUBY_SUCCESS_URL", ""),
        suby_cancel_url=os.getenv("SUBY_CANCEL_URL", ""),
        suby_price_cents=int(os.getenv("SUBY_PRICE_CENTS", "2900")),
        suby_currency=os.getenv("SUBY_CURRENCY", "USD"),
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
