"""Data-access functions. One file, plain SQL, async."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from database.connection import pool


# ─── users ────────────────────────────────────────────────────────────────────
async def upsert_user(discord_id: int, username: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            INSERT INTO users (discord_id, discord_username)
            VALUES ($1, $2)
            ON CONFLICT (discord_id)
            DO UPDATE SET discord_username = EXCLUDED.discord_username
            """,
            discord_id, username,
        )


async def get_user(discord_id: int) -> Optional[dict]:
    async with pool().acquire() as c:
        row = await c.fetchrow("SELECT * FROM users WHERE discord_id = $1", discord_id)
        return dict(row) if row else None


async def mark_subscribed(discord_id: int, customer_id: str, subscription_id: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            UPDATE users
               SET subscription_status = 'pro',
                   suby_customer_id = $2,
                   suby_subscription_id = $3,
                   subscribed_at = NOW(),
                   cancelled_at = NULL
             WHERE discord_id = $1
            """,
            discord_id, customer_id, subscription_id,
        )


async def mark_cancelled(discord_id: int) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            UPDATE users
               SET subscription_status = 'cancelled',
                   cancelled_at = NOW()
             WHERE discord_id = $1
            """,
            discord_id,
        )


async def get_user_by_customer_id(customer_id: str) -> Optional[dict]:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT * FROM users WHERE suby_customer_id = $1",
            customer_id,
        )
        return dict(row) if row else None


# ─── modal credentials ────────────────────────────────────────────────────────
async def save_modal_credentials(discord_id: int, token_id_enc: str, token_secret_enc: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            INSERT INTO modal_credentials (discord_id, token_id_encrypted, token_secret_encrypted, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (discord_id) DO UPDATE
              SET token_id_encrypted = EXCLUDED.token_id_encrypted,
                  token_secret_encrypted = EXCLUDED.token_secret_encrypted,
                  updated_at = NOW()
            """,
            discord_id, token_id_enc, token_secret_enc,
        )


async def set_modal_app_url(discord_id: int, url: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            UPDATE modal_credentials
               SET modal_app_url = $2,
                   deployed_at = NOW(),
                   updated_at = NOW()
             WHERE discord_id = $1
            """,
            discord_id, url,
        )


async def get_modal_credentials(discord_id: int) -> Optional[dict]:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT * FROM modal_credentials WHERE discord_id = $1",
            discord_id,
        )
        return dict(row) if row else None


# ─── pending checkouts (Suby paymentId → discord_id) ─────────────────────────
async def record_pending_checkout(payment_id: str, discord_id: int) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            INSERT INTO pending_checkouts (payment_id, discord_id)
            VALUES ($1, $2)
            ON CONFLICT (payment_id) DO UPDATE SET discord_id = EXCLUDED.discord_id
            """,
            payment_id, discord_id,
        )


async def lookup_discord_id_by_payment(payment_id: str) -> Optional[int]:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT discord_id FROM pending_checkouts WHERE payment_id = $1",
            payment_id,
        )
        return int(row["discord_id"]) if row else None


async def delete_pending_checkout(payment_id: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            "DELETE FROM pending_checkouts WHERE payment_id = $1",
            payment_id,
        )


# ─── video jobs ───────────────────────────────────────────────────────────────
async def has_processing_job(discord_id: int) -> bool:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT 1 FROM video_jobs WHERE discord_id = $1 AND status = 'processing' LIMIT 1",
            discord_id,
        )
        return row is not None


async def create_job(discord_id: int, prompt: str, extended_prompt: str | None) -> int:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """
            INSERT INTO video_jobs (discord_id, prompt, extended_prompt, status)
            VALUES ($1, $2, $3, 'processing')
            RETURNING id
            """,
            discord_id, prompt, extended_prompt,
        )
        return row["id"]


async def set_job_api_id(job_id: int, api_job_id: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            "UPDATE video_jobs SET api_job_id = $2 WHERE id = $1",
            job_id, api_job_id,
        )


async def complete_job(job_id: int, video_url: str) -> Optional[dict]:
    """Mark job done. Idempotent: returns row only if state actually transitioned."""
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """
            UPDATE video_jobs
               SET status = 'done',
                   video_url = $2,
                   completed_at = NOW()
             WHERE id = $1 AND status = 'processing'
            RETURNING *
            """,
            job_id, video_url,
        )
        return dict(row) if row else None


async def fail_job(job_id: int, error: str) -> Optional[dict]:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """
            UPDATE video_jobs
               SET status = 'failed',
                   error_message = $2,
                   completed_at = NOW()
             WHERE id = $1 AND status = 'processing'
            RETURNING *
            """,
            job_id, error,
        )
        return dict(row) if row else None


async def get_job(job_id: int) -> Optional[dict]:
    async with pool().acquire() as c:
        row = await c.fetchrow("SELECT * FROM video_jobs WHERE id = $1", job_id)
        return dict(row) if row else None


async def list_recent_videos(discord_id: int, limit: int = 5) -> list[dict]:
    async with pool().acquire() as c:
        rows = await c.fetch(
            """
            SELECT id, prompt, status, video_url, started_at, completed_at
              FROM video_jobs
             WHERE discord_id = $1 AND status = 'done'
             ORDER BY completed_at DESC NULLS LAST
             LIMIT $2
            """,
            discord_id, limit,
        )
        return [dict(r) for r in rows]


async def count_videos(discord_id: int) -> int:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT COUNT(*) AS n FROM video_jobs WHERE discord_id = $1 AND status = 'done'",
            discord_id,
        )
        return int(row["n"])


# ─── user preferences (style + voice) ────────────────────────────────────────
DEFAULT_STYLE_PRESET = "whiteboard"
DEFAULT_VOICE = "af_heart"


async def get_user_preferences(discord_id: int) -> dict:
    """Returns prefs row or sensible defaults if the user never set anything.
    `style_custom` is always a dict-or-None (asyncpg returns JSONB as str)."""
    import json
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT * FROM user_preferences WHERE discord_id = $1",
            discord_id,
        )
    if row:
        d = dict(row)
        sc = d.get("style_custom")
        if isinstance(sc, str):
            try:
                d["style_custom"] = json.loads(sc)
            except json.JSONDecodeError:
                d["style_custom"] = None
        return d
    return {
        "discord_id": discord_id,
        "style_preset_key": DEFAULT_STYLE_PRESET,
        "style_custom": None,
        "voice": DEFAULT_VOICE,
        "updated_at": None,
    }


async def set_style_preset(discord_id: int, preset_key: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            INSERT INTO user_preferences (discord_id, style_preset_key, style_custom, voice, updated_at)
            VALUES ($1, $2, NULL, COALESCE(
                (SELECT voice FROM user_preferences WHERE discord_id = $1), 'af_heart'
            ), NOW())
            ON CONFLICT (discord_id) DO UPDATE
              SET style_preset_key = EXCLUDED.style_preset_key,
                  style_custom = NULL,
                  updated_at = NOW()
            """,
            discord_id, preset_key,
        )


async def set_style_custom(discord_id: int, custom: dict) -> None:
    import json
    async with pool().acquire() as c:
        await c.execute(
            """
            INSERT INTO user_preferences (discord_id, style_preset_key, style_custom, voice, updated_at)
            VALUES ($1, NULL, $2::jsonb, COALESCE(
                (SELECT voice FROM user_preferences WHERE discord_id = $1), 'af_heart'
            ), NOW())
            ON CONFLICT (discord_id) DO UPDATE
              SET style_preset_key = NULL,
                  style_custom = EXCLUDED.style_custom,
                  updated_at = NOW()
            """,
            discord_id, json.dumps(custom),
        )


async def set_voice(discord_id: int, voice: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """
            INSERT INTO user_preferences (discord_id, voice, style_preset_key, updated_at)
            VALUES ($1, $2, 'whiteboard', NOW())
            ON CONFLICT (discord_id) DO UPDATE
              SET voice = EXCLUDED.voice,
                  updated_at = NOW()
            """,
            discord_id, voice,
        )


async def current_processing_job(discord_id: int) -> Optional[dict]:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """
            SELECT * FROM video_jobs
             WHERE discord_id = $1 AND status = 'processing'
             ORDER BY started_at DESC
             LIMIT 1
            """,
            discord_id,
        )
        return dict(row) if row else None
