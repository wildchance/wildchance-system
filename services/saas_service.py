"""SaaS service — signup, API-key resolution, tier changes, usage (DB glue)."""

from __future__ import annotations

import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user_model import User
from saas import tiers as st


def _new_key() -> str:
    return "wc_" + secrets.token_urlsafe(24)


def _to_dict(u: User) -> dict:
    t = st.get_tier(u.tier) or {}
    return {"id": u.id, "email": u.email, "api_key": u.api_key, "tier": u.tier,
            "tier_name": t.get("name"), "account_limit": st.account_limit(u.tier),
            "active": bool(u.active), "calls_this_period": u.calls_this_period or 0,
            "created_at": str(u.created_at) if u.created_at else None}


async def signup(db: AsyncSession, email: str, tier: str = "scout") -> dict:
    """Create a user (idempotent on email) and mint an API key."""
    res = await db.execute(select(User).where(User.email == email))
    existing = res.scalars().first()
    if existing:
        return {"existing": True, **_to_dict(existing)}
    if not st.get_tier(tier):
        tier = "scout"
    u = User(email=email, api_key=_new_key(), tier=tier, active=True, calls_this_period=0)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return {"existing": False, **_to_dict(u)}


async def by_api_key(db: AsyncSession, api_key: str) -> Optional[User]:
    if not api_key:
        return None
    res = await db.execute(select(User).where(User.api_key == api_key, User.active.is_(True)))
    return res.scalars().first()


async def me(db: AsyncSession, api_key: str) -> Optional[dict]:
    u = await by_api_key(db, api_key)
    return _to_dict(u) if u else None


async def set_tier(db: AsyncSession, email: str, tier: str) -> dict:
    """Change a user's tier (called by the billing webhook on a successful payment)."""
    if not st.get_tier(tier):
        return {"error": f"unknown tier {tier}", "tiers": [t['key'] for t in st.TIERS]}
    res = await db.execute(select(User).where(User.email == email))
    u = res.scalars().first()
    if not u:
        return {"error": f"no user {email}"}
    u.tier = tier
    await db.commit()
    await db.refresh(u)
    return _to_dict(u)


async def rotate_key(db: AsyncSession, api_key: str) -> Optional[dict]:
    u = await by_api_key(db, api_key)
    if not u:
        return None
    u.api_key = _new_key()
    await db.commit()
    await db.refresh(u)
    return _to_dict(u)
