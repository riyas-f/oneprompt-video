"""Read-only Modal account info for /balance.

Modal exposes a GraphQL endpoint at https://api.modal.com/graphql. Token auth
uses the same MODAL_TOKEN_ID / MODAL_TOKEN_SECRET pair we already store.
The exact field names occasionally drift across Modal API versions, so we
try a small set and gracefully degrade if none match.
"""
from __future__ import annotations

import httpx

from config import cfg


async def fetch_balance(token_id: str, token_secret: str) -> dict:
    """Returns {balance_usd: float|None, raw: dict}. Never raises on field misses."""
    query = """
    query Me {
      currentUser {
        username
        workspaces {
          name
          balanceUsd
          monthToDateUsageUsd
        }
      }
    }
    """
    headers = {
        "Modal-Key": token_id,
        "Modal-Secret": token_secret,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            cfg.modal_graphql_url,
            json={"query": query},
            headers=headers,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Modal API returned {r.status_code}: {r.text[:200]}")

    data = r.json().get("data") or {}
    user = data.get("currentUser") or {}
    workspaces = user.get("workspaces") or []
    primary = workspaces[0] if workspaces else {}
    return {
        "username": user.get("username"),
        "workspace": primary.get("name"),
        "balance_usd": primary.get("balanceUsd"),
        "mtd_usage_usd": primary.get("monthToDateUsageUsd"),
        "raw": data,
    }
