"""asyncpg connection pool + a tiny query helper. Plain SQL, no ORM bloat."""
from __future__ import annotations

import os
import asyncpg
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from config import cfg

_pool: asyncpg.Pool | None = None


def _to_asyncpg_dsn(url: str) -> str:
    # SQLAlchemy-style "postgresql+asyncpg://" -> asyncpg-style "postgresql://"
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    return url


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=_to_asyncpg_dsn(cfg.database_url),
            min_size=1,
            max_size=10,
        )
        await _run_migrations(_pool)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call init_pool() first")
    return _pool


async def _run_migrations(p: asyncpg.Pool) -> None:
    sql_path = Path(__file__).parent / "migrations.sql"
    sql = sql_path.read_text(encoding="utf-8")
    async with p.acquire() as conn:
        await conn.execute(sql)
