"""EA → app data bridge. The MT5 EA pushes closed broker bars + an account snapshot.

  POST /bridge/bars              {"symbol":"XAUUSD.r","timeframe":"H1","bars":[{t,o,h,l,c,v}]}
  POST /bridge/account           {"login":…, "balance":…, "equity":…, "guard":"ACTIVE", …}
  POST /bridge/deals             closed trades → profit flash cards (winners only, once each)
  GET  /bridge/status           what has arrived, how fresh, last account snapshots
  POST /bridge/copy/master      copy trader: master positions · GET /bridge/copy (follower targets)
  POST /bridge/copy/report      follower state
  GET  /bridge/gate             the EA asks before every entry: council + exposure + regime + calendar

Auth: BRIDGE_TOKEN (falls back to EXECUTION_TOKEN) in the X-Bridge-Token HEADER only —
a ?token= query parameter is ignored so the secret never lands in request logs.
Every bar push also runs the real-time engines for that symbol (services.realtime).
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from decouple import config
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from services import broker_feed

router = APIRouter(prefix="/bridge", tags=["bridge"])

TOKEN = config("BRIDGE_TOKEN", default=None) or config("EXECUTION_TOKEN", default=None)
_accounts: Dict[str, Dict] = {}


_owner_keys: Dict[str, object] = {"loaded": False, "keys": set()}


def derived_owner_key() -> Optional[str]:
    """The owner's EA key, COMPUTED from a server secret already in Render (OPS_TOKEN, else
    CRON_TOKEN, else the Telegram bot token) — nothing stored, so a database hiccup or a second
    /license can never mint a different key and orphan the one pasted into the EA. Rotating that
    secret rotates the key."""
    import hashlib
    import hmac
    secret = (config("OPS_TOKEN", default=None) or config("CRON_TOKEN", default=None)
              or config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None))
    if not secret:
        return None
    return "wco_" + hmac.new(str(secret).encode(), b"wildchance-owner-ea-license", hashlib.sha256).hexdigest()[:32]


async def owner_license() -> str:
    """The owner's EA license for the bot's /license — always the same derived key."""
    k = derived_owner_key()
    if k:
        return k
    # no server secret at all (unusual): fall back to a stored key, created only if truly absent
    import secrets
    from services import data_store
    v, _ = await data_store.get("owner_license")
    if not v:
        v = "wco_" + secrets.token_urlsafe(24)
        if not await data_store.put("owner_license", v):
            raise RuntimeError("could not save the owner license — try /license again in a minute")
    _owner_keys["keys"].add(v)
    _owner_keys["loaded"] = True
    return v


def hmac_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(str(a), str(b))


async def is_owner_key(token: Optional[str]) -> bool:
    if not token:
        return False
    if TOKEN and token == TOKEN:
        return True
    dk = derived_owner_key()
    if dk and hmac_equal(token, dk):
        return True
    if not _owner_keys["loaded"]:
        try:
            from services import data_store
            v, _ = await data_store.get("owner_license")
            if v:
                _owner_keys["keys"].add(v)
            _owner_keys["loaded"] = True
        except Exception:
            return False
    return token in _owner_keys["keys"]


async def _auth(token: Optional[str]):
    """Owner-only routes (bars, account, deals, calendar, copy, export)."""
    if not await is_owner_key(token):
        raise HTTPException(403, "bad bridge token")


GATE_MIN_TIER = config("EA_LICENSE_MIN_TIER", default="trader")


async def principal(token: Optional[str]) -> Dict:
    """Who is this EA? The owner's BRIDGE_TOKEN → owner (full feed). A member's API key with
    a live paid / trial plan at or above EA_LICENSE_MIN_TIER → member (gate only: their
    trades never reach the owner's journal, flash cards or channel)."""
    import datetime as _dt
    if await is_owner_key(token):
        return {"role": "owner"}
    if not token:
        raise HTTPException(401, "license key missing")
    from database.db import AsyncSessionLocal
    from saas import tiers as st
    from services import saas_service as svc
    async with AsyncSessionLocal() as db:
        u = await svc.by_api_key(db, token)
    if not u:
        raise HTTPException(403, "license key not recognised")
    pu = u.paid_until
    if pu is not None and pu.tzinfo is None:
        pu = pu.replace(tzinfo=_dt.timezone.utc)
    live = pu is not None and pu > _dt.datetime.now(_dt.timezone.utc)
    if not live or st.rank_of(u.tier) < st.rank_of(GATE_MIN_TIER):
        raise HTTPException(402, f"license inactive — plan {u.tier}, renew at /pricing")
    return {"role": "member", "email": u.email, "tier": u.tier, "valid_until": pu.date().isoformat()}


class BarsIn(BaseModel):
    symbol: str
    timeframe: str
    bars: List[Dict] = Field(default_factory=list)


@router.post("/bars")
async def bars(body: BarsIn, background: BackgroundTasks,
               x_bridge_token: Optional[str] = Header(None),
               db: AsyncSession = Depends(get_db)):
    await _auth(x_bridge_token)
    res = await broker_feed.ingest(db, body.symbol, body.timeframe, body.bars)
    if res.get("ok"):
        from services.realtime import on_bars
        background.add_task(on_bars, res["symbol"], res["timeframe"])
    return res


@router.post("/account")
async def account(snapshot: Dict,
                  x_bridge_token: Optional[str] = Header(None)):
    await _auth(x_bridge_token)
    key = str(snapshot.get("login") or "default")
    _accounts[key] = {**snapshot, "received_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    return {"ok": True}


class DealsIn(BaseModel):
    deals: List[Dict] = Field(default_factory=list)


@router.post("/deals")
async def deals(body: DealsIn, background: BackgroundTasks,
                x_bridge_token: Optional[str] = Header(None)):
    """Closed trades from MT5 history → profit flash cards (winners only, once each).
    deal: {ticket, symbol, side (BUY/SELL of the POSITION), volume, open_price,
           close_price, open_time, close_time (unix), net (profit+commission+swap)}"""
    await _auth(x_bridge_token)
    from services.flash_card import post_from_deals
    from services.trade_journal import record
    background.add_task(record, body.deals)               # journal: every close, W / L / BE
    background.add_task(post_from_deals, body.deals)      # flash cards: winners only
    return {"ok": True, "received": len(body.deals)}


_gate_cache: Dict[str, tuple] = {}
GATE_CACHE_S = 60


async def gate_verdict(symbol: str, side: str) -> Dict:
    """One flat answer for the EA: allow (bool), risk_pct (app risk budget), reason.
    Council GO + currency exposure caps + regime routing (council_gate.check) and the
    app's economic-calendar blackout (Forex Factory / owner paste / EA calendar)."""
    import time
    from services import council_gate, econ_calendar
    sym = broker_feed.canon(symbol).replace("/", "")
    sd = council_gate.norm_side(side)
    key = f"{sym}:{sd}"
    hit = _gate_cache.get(key)
    if hit and time.time() - hit[0] < GATE_CACHE_S:
        return hit[1]
    pair = f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 and sym.isalpha() else sym
    try:
        await econ_calendar.ensure_loaded()
        bo = econ_calendar.blackout(pair)
    except Exception:
        bo = None
    g = await council_gate.check(pair, sd)
    allow = bool(g.get("pass")) and not bo
    reason = bo or ("GO" if allow else str(g.get("verdict") or "BLOCKED"))
    out = {"allow": allow, "verdict": "EVENT" if bo else g.get("verdict"), "reason": reason[:60],
           "risk_pct": g.get("risk_pct") or 0, "regime": g.get("regime") or "",
           "for": g.get("for"), "against": g.get("against"), "symbol": pair, "side": sd}
    _gate_cache[key] = (time.time(), out)
    return out


@router.get("/gate")
async def gate(symbol: str = Query(...), side: str = Query(...),
               x_bridge_token: Optional[str] = Header(None)):
    """Read-only council verdict for the EA — no key needed (cached 60 s per symbol/side)."""
    return await gate_verdict(symbol, side)


@router.post("/setup")
async def ea_setup(ev: Dict, x_bridge_token: Optional[str] = Header(None)):
    """Every setup the owner's EA finds (entered or blocked + why) → the setup log."""
    await _auth(x_bridge_token)
    from services import ea_setups
    return await ea_setups.record(ev)


@router.get("/whoami")
async def whoami(x_bridge_token: Optional[str] = Header(None)):
    """The EA's start-up license check: role (owner / member), plan and expiry."""
    return await principal(x_bridge_token)


# ── copy trader (WildChance_Copier EA) ─────────────────────────────────────────
@router.post("/copy/master")
async def copy_master(snapshot: Dict, x_bridge_token: Optional[str] = Header(None)):
    """MASTER account: every open position (any source) every ~2 s."""
    await _auth(x_bridge_token)
    from services import copy_trade
    return await copy_trade.set_master(snapshot)


@router.get("/copy")
async def copy_targets(login: str = Query(...), equity: float = Query(..., ge=0), ccy: str = Query("USD"),
                       x_bridge_token: Optional[str] = Header(None)):
    """FOLLOWER account: the positions it should hold now (equity-scaled volumes)."""
    await _auth(x_bridge_token)
    from services import copy_trade
    return await copy_trade.targets(login, equity, ccy)


@router.post("/copy/report")
async def copy_report(rep: Dict, x_bridge_token: Optional[str] = Header(None)):
    """FOLLOWER account: what it holds, lag, guard state, last errors → /copy/status + bot /accounts."""
    await _auth(x_bridge_token)
    from services import copy_trade
    return await copy_trade.report(rep)


@router.get("/status")
async def status():
    try:
        feed = await broker_feed.status()
    except Exception as e:
        feed = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {"feed": feed, "accounts": list(_accounts.values())}


@router.get("/export")
async def export(symbol: str = Query(...), timeframe: str = Query("D1"),
                 n: int = Query(3000, ge=10, le=20000),
                 x_bridge_token: Optional[str] = Header(None),
                 db: AsyncSession = Depends(get_db)):
    """Broker bars oldest-first for offline research (backtest/bb_sweep.py on a compute box)."""
    await _auth(x_bridge_token)
    rows = await broker_feed.recent(db, symbol, timeframe.upper(), n)
    bars = [{"t": int(r.ts.replace(tzinfo=r.ts.tzinfo or dt.timezone.utc).timestamp()),
             "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume}
            for r in reversed(rows)]
    return {"symbol": broker_feed.canon(symbol), "timeframe": timeframe.upper(),
            "count": len(bars), "bars": bars}


class CalendarIn(BaseModel):
    events: List[Dict] = Field(default_factory=list)


@router.post("/calendar")
async def calendar(body: CalendarIn,
                   x_bridge_token: Optional[str] = Header(None)):
    """The EA's MT5 economic calendar (broker-grade, free) — backs up the ForexFactory feed.
    events: [{t: unix UTC, ccy, event, impact: high|medium|low, actual, forecast, previous}]"""
    await _auth(x_bridge_token)
    from wildchance import wildchance_scraper as wc
    n = wc.set_ea_calendar(body.events)
    return {"ok": True, "events": n}


@router.get("/calendar")
async def calendar_view():
    from wildchance import wildchance_scraper as wc
    return wc.calendar_status()
