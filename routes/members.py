"""Members area API (the /members page calls these with the member's API key)."""

from __future__ import annotations

from decouple import config
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from saas.auth import current_user
from saas import tiers as st

router = APIRouter(prefix="/members", tags=["members"])


@router.get("/me")
async def me(user=Depends(current_user)):
    paid = st.rank_of(user.tier) >= 1
    return {"email": user.email, "tier": user.tier, "paid": paid,
            "paid_until": user.paid_until.isoformat() if user.paid_until else None,
            "telegram_invite": config("MEMBERS_TELEGRAM_INVITE", default=None) if paid else None}


@router.get("/feed")
async def feed(limit: int = Query(30, ge=1, le=100), user=Depends(current_user),
               db: AsyncSession = Depends(get_db)):
    from services.signal_feed import recent
    paid = st.rank_of(user.tier) >= 1
    rows = await recent(db, limit if paid else 3)
    if not paid:                                   # free tier: delayed teaser only
        rows = [{**r, "text": r["text"][:90] + "… (upgrade to see entries, stops and targets)"}
                for r in rows]
    return {"tier": user.tier, "signals": rows}
