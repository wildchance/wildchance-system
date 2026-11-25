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


def _auth(token: Optional[str]):
    if not TOKEN:
        raise HTTPException(503, "bridge disabled — set BRIDGE_TOKEN (or EXECUTION_TOKEN)")
    if token != TOKEN:
        raise HTTPException(403, "bad bridge token")


class BarsIn(BaseModel):
    symbol: str
    timeframe: str
    bars: List[Dict] = Field(default_factory=list)


@router.post("/bars")
async def bars(body: BarsIn, background: BackgroundTasks,
               x_bridge_token: Optional[str] = Header(None),
               db: AsyncSession = Depends(get_db)):
    _auth(x_bridge_token)
    res = await broker_feed.ingest(db, body.symbol, body.timeframe, body.bars)
    if res.get("ok"):
        from services.realtime import on_bars
        background.add_task(on_bars, res["symbol"], res["timeframe"])
    return res


@router.post("/account")
async def account(snapshot: Dict,
                  x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
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
    _auth(x_bridge_token)
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
    _auth(x_bridge_token)
    return await gate_verdict(symbol, side)


# ── copy trader (WildChance_Copier EA) ─────────────────────────────────────────
@router.post("/copy/master")
async def copy_master(snapshot: Dict, x_bridge_token: Optional[str] = Header(None)):
    """MASTER account: every open position (any source) every ~2 s."""
    _auth(x_bridge_token)
    from services import copy_trade
    return await copy_trade.set_master(snapshot)


@router.get("/copy")
async def copy_targets(login: str = Query(...), equity: float = Query(..., ge=0), ccy: str = Query("USD"),
                       x_bridge_token: Optional[str] = Header(None)):
    """FOLLOWER account: the positions it should hold now (equity-scaled volumes)."""
    _auth(x_bridge_token)
    from services import copy_trade
    return await copy_trade.targets(login, equity, ccy)


@router.post("/copy/report")
async def copy_report(rep: Dict, x_bridge_token: Optional[str] = Header(None)):
    """FOLLOWER account: what it holds, lag, guard state, last errors → /copy/status + bot /accounts."""
    _auth(x_bridge_token)
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
    _auth(x_bridge_token)
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
    _auth(x_bridge_token)
    from wildchance import wildchance_scraper as wc
    n = wc.set_ea_calendar(body.events)
    return {"ok": True, "events": n}


@router.get("/calendar")
async def calendar_view():
    from wildchance import wildchance_scraper as wc
    return wc.calendar_status()
