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
    polar_product_id_pro: str
    polar_product_id_studio: str
    polar_api_base: str
    polar_success_url: str
    polar_trial_days: int
    # Quotas are RENDER-MINUTES per billing period (a 3-min video spends 3).
    pro_monthly_minutes: int
    studio_monthly_minutes: int
    trial_minutes: int
    max_hosted_videos_per_day: int
    # BYO Modal (/setmodal) is Enterprise-only now; hidden unless enabled.
    enable_byo: bool

    encryption_key: str

    modal_app_path: str
    modal_graphql_url: str
    admin_modal_url: str
    free_generations: int

    video_api_url: str
    video_api_callback_secret: str
    bot_public_url: str

    # Internal pipeline (PIPELINE=internal) vs legacy n8n dispatch (PIPELINE=n8n).
    pipeline: str
    openrouter_api_key: str
    openrouter_base: str
    openrouter_model: str
    r2_endpoint: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    r2_public_base_url: str
    job_timeout_minutes: int
    pipeline_max_concurrent: int
    # Shared secret between bot and the Modal render app (async endpoints).
    render_api_key: str

    # Whop embedded app (all optional; /whop mounts only when api key is set).
    whop_api_key: str
    whop_app_id: str
    whop_plan_pro: str
    whop_plan_studio: str
    whop_webhook_secret: str

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
        polar_product_id_pro=_req("POLAR_PRODUCT_ID_PRO"),
        polar_product_id_studio=_req("POLAR_PRODUCT_ID_STUDIO"),
        polar_api_base=os.getenv("POLAR_API_BASE", "https://api.polar.sh"),
        polar_success_url=os.getenv("POLAR_SUCCESS_URL", ""),
        polar_trial_days=int(os.getenv("POLAR_TRIAL_DAYS", "7")),
        pro_monthly_minutes=int(os.getenv("PRO_MONTHLY_MINUTES", "60")),
        studio_monthly_minutes=int(os.getenv("STUDIO_MONTHLY_MINUTES", "200")),
        trial_minutes=int(os.getenv("TRIAL_MINUTES", "10")),
        max_hosted_videos_per_day=int(os.getenv("MAX_HOSTED_VIDEOS_PER_DAY", "200")),
        enable_byo=os.getenv("ENABLE_BYO", "false").strip().lower() in ("1", "true", "yes"),
        encryption_key=_req("ENCRYPTION_KEY"),
        modal_app_path=os.getenv("MODAL_APP_PATH", "./modal_app/app.py"),
        modal_graphql_url=os.getenv("MODAL_GRAPHQL_URL", "https://api.modal.com/graphql"),
        admin_modal_url=_req("ADMIN_MODAL_URL"),
        free_generations=int(os.getenv("FREE_GENERATIONS", "2")),
        video_api_url=_req("VIDEO_API_URL"),
        video_api_callback_secret=_req("VIDEO_API_CALLBACK_SECRET"),
        bot_public_url=_req("BOT_PUBLIC_URL"),
        pipeline=os.getenv("PIPELINE", "n8n").strip().lower(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_base=os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1"),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
        r2_endpoint=os.getenv("R2_ENDPOINT", ""),
        r2_access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
        r2_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
        r2_bucket=os.getenv("R2_BUCKET", ""),
        r2_public_base_url=os.getenv("R2_PUBLIC_BASE_URL", ""),
        job_timeout_minutes=int(os.getenv("JOB_TIMEOUT_MINUTES", "45")),
        pipeline_max_concurrent=int(os.getenv("PIPELINE_MAX_CONCURRENT", "4")),
        render_api_key=os.getenv("RENDER_API_KEY", ""),
        whop_api_key=os.getenv("WHOP_API_KEY", ""),
        whop_app_id=os.getenv("WHOP_APP_ID", ""),
        whop_plan_pro=os.getenv("WHOP_PLAN_PRO", ""),
        whop_plan_studio=os.getenv("WHOP_PLAN_STUDIO", ""),
        whop_webhook_secret=os.getenv("WHOP_WEBHOOK_SECRET", ""),
        support_user_id=int(_req("SUPPORT_DISCORD_USER_ID")),
        webhook_host=os.getenv("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
        price_usd=int(os.getenv("PRICE_USD", "29")),
    )


cfg = load()
