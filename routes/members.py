"""Members area API (the /members page calls these with the member's API key)."""

from __future__ import annotations

from decouple import config
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from saas.auth import current_user
from saas import tiers as st

import os as _os

from fastapi.responses import FileResponse as _FileResponse, JSONResponse as _JSONResponse

EA_FILE = "static/ea/WildChance_EA.ex5"

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


class PasswordIn(BaseModel):
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/password")
async def set_password(body: PasswordIn, user=Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    """Set / change the password (authenticated with the member's API key)."""
    from saas.passwords import hash_password, strong_enough, MIN_LEN
    if not strong_enough(body.password):
        raise HTTPException(status_code=400, detail=f"use at least {MIN_LEN} characters (not only digits)")
    user.password_hash = hash_password(body.password)
    db.add(user)
    await db.commit()
    return {"ok": True}


@router.post("/login")
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Email + password → the member's API key (the page then works as before).
    One generic error for every failure; 5 failures / 15 min locks email and IP."""
    from sqlalchemy import func, select
    from models.user_model import User
    from saas import passwords as pw
    email = (body.email or "").strip().lower()
    ip = request.client.host if request.client else "?"
    keys = (f"e:{email}", f"ip:{ip}")
    if pw.locked(*keys):
        raise HTTPException(status_code=429, detail="too many attempts — try again in 15 minutes")
    u = (await db.execute(select(User).where(func.lower(User.email) == email))).scalars().first()
    ok = bool(u and u.active and u.password_hash and pw.verify_password(body.password, u.password_hash))
    if not ok:
        if not u:                                       # same work either way (no timing oracle)
            pw.verify_password(body.password, pw.hash_password("dummy-password", 1000))
        pw.record_fail(*keys)
        raise HTTPException(status_code=401, detail="email or password not recognised")
    pw.clear(*keys)
    return {"api_key": u.api_key, "email": u.email, "tier": u.tier}


@router.get("/ea/download")
async def ea_download():
    """The compiled EA (.ex5) for members — one file to copy into MQL5/Experts."""
    if not _os.path.exists(EA_FILE):
        return _JSONResponse({"ok": False, "reason": "the EA download is being prepared — ask in the bot"},
                             status_code=404)
    return _FileResponse(EA_FILE, filename="WildChance_EA.ex5", media_type="application/octet-stream")
