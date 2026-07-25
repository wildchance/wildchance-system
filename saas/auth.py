"""SaaS auth dependencies + the feature/path → tier gate map.

- require_api_key: resolve the caller's user from the X-API-Key header (or ?api_key=).
- require_tier(min): FastAPI dependency that 402s if the caller is below ``min``.
- tier_for_path(path): the minimum tier a gold endpoint needs (used by the middleware).
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from services import saas_service as svc
from saas import tiers as st

# Path-prefix → minimum tier. Only these premium prefixes are gated; everything else
# (scan, plan, health, docs, saas) is open. Longest-prefix match wins.
FEATURE_TIERS = {
    "/gold/kingdom-report": "pro",
    "/gold/volatility": "pro",
    "/gold/intermarket": "pro",
    "/gold/trap": "pro",
    "/gold/backtest": "pro",
    "/gold/optimus": "fleet",
    "/gold/bumblebee": "fleet",
    "/gold/venom": "fleet",
    "/gold/network": "fleet",
    "/gold/accounts/fanout": "fleet",
    "/execution": "fleet",
}


def tier_for_path(path: str) -> Optional[str]:
    """The minimum tier for a path (longest matching prefix), or None if open."""
    best = None
    for prefix, tier in FEATURE_TIERS.items():
        if path.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, tier)
    return best[1] if best else None


async def current_user(request: Request,
                       x_api_key: Optional[str] = Header(None),
                       api_key: Optional[str] = Query(None),
                       db: AsyncSession = Depends(get_db)):
    key = x_api_key or api_key
    u = await svc.by_api_key(db, key) if key else None
    if not u:
        raise HTTPException(status_code=401, detail="missing or invalid API key")
    return u


def require_tier(min_tier: str):
    async def _dep(user=Depends(current_user)):
        if not st.tier_allows(user.tier, min_tier):
            raise HTTPException(
                status_code=402,
                detail=f"upgrade required — this feature needs the {min_tier} tier "
                       f"(you are on {user.tier})")
        return user
    return _dep
