"""Subscription state changes (DB glue for verified billing events)."""

from __future__ import annotations

import datetime as dt
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user_model import User
from saas import tiers as st


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def _user(db: AsyncSession, email: str) -> Optional[User]:
    return (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()


async def activate(db: AsyncSession, email: str, tier: str, months: int = 1,
                   provider: str = "manual", ref: Optional[str] = None,
                   customer: Optional[str] = None) -> Dict:
    """Paid → tier on, paid_until extended by 31 days per month (stacks on time left).
    Creates the account if the payer never signed up (keys are shown on /members)."""
    if not st.get_tier(tier) or tier == "scout":
        return {"ok": False, "reason": f"unknown or free tier {tier!r}"}
    u = await _user(db, email)
    if u is None:
        from services.saas_service import _new_key
        u = User(email=email.lower(), api_key=_new_key(), tier="scout", active=True,
                 calls_this_period=0)
        db.add(u)
    now = _utcnow()
    base = u.paid_until if u.paid_until and u.paid_until.replace(tzinfo=u.paid_until.tzinfo or dt.timezone.utc) > now else now
    base = base if base.tzinfo else base.replace(tzinfo=dt.timezone.utc)
    u.tier, u.provider, u.provider_ref = tier, provider, ref
    u.paid_until = base + dt.timedelta(days=31 * max(1, int(months)))
    if customer:
        u.stripe_customer_id = customer
    await db.commit()
    return {"ok": True, "email": u.email, "tier": u.tier, "paid_until": u.paid_until.isoformat()}


async def start_trial(db: AsyncSession, email: str, tier: str, days: int, ref: str) -> Dict:
    """Free trial: tier on for ``days`` — only for someone with no paid time left (never
    shortens or replaces a paid subscription). One-per-person is enforced by the caller."""
    if not st.get_tier(tier) or tier == "scout":
        return {"ok": False, "reason": f"unknown or free tier {tier!r}"}
    u = await _user(db, email)
    now = _utcnow()
    if u is not None and u.paid_until:
        pu = u.paid_until if u.paid_until.tzinfo else u.paid_until.replace(tzinfo=dt.timezone.utc)
        if pu > now:
            return {"ok": False, "reason": "this email already has an active plan"}
    if u is None:
        from services.saas_service import _new_key
        u = User(email=email.lower(), api_key=_new_key(), tier="scout", active=True, calls_this_period=0)
        db.add(u)
    u.tier, u.provider, u.provider_ref = tier, "trial", ref
    u.paid_until = now + dt.timedelta(days=max(1, int(days)))
    await db.commit()
    return {"ok": True, "email": u.email, "tier": u.tier, "paid_until": u.paid_until.isoformat()}


async def cancel_now(db: AsyncSession, email: Optional[str] = None,
                     customer: Optional[str] = None) -> Dict:
    q = select(User).where(User.email == email.lower()) if email else \
        select(User).where(User.stripe_customer_id == customer)
    u = (await db.execute(q)).scalar_one_or_none()
    if not u:
        return {"ok": False, "reason": "no such member"}
    u.tier, u.paid_until = "scout", _utcnow()
    await db.commit()
    return {"ok": True, "email": u.email, "tier": "scout"}


async def expire_due(db: AsyncSession) -> Dict:
    """Daily: members whose paid_until has passed drop to the free tier."""
    now = _utcnow()
    rows = (await db.execute(select(User).where(User.tier != "scout",
                                                User.paid_until.isnot(None)))).scalars().all()
    expired = []
    for u in rows:
        pu = u.paid_until if u.paid_until.tzinfo else u.paid_until.replace(tzinfo=dt.timezone.utc)
        if pu < now:
            u.tier = "scout"
            expired.append(u.email)
    if expired:
        await db.commit()
    return {"expired": len(expired)}
