"""EA → app data bridge. The MT5 EA pushes closed broker bars + an account snapshot.

  POST /bridge/bars?token=…     {"symbol":"XAUUSD.r","timeframe":"H1","bars":[{t,o,h,l,c,v}]}
  POST /bridge/account?token=…  {"login":…, "balance":…, "equity":…, "guard":"ACTIVE", …}
  GET  /bridge/status           what has arrived, how fresh, last account snapshots

Auth: BRIDGE_TOKEN (falls back to EXECUTION_TOKEN). Without either → 503.
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
               token: Optional[str] = Query(None),
               x_bridge_token: Optional[str] = Header(None),
               db: AsyncSession = Depends(get_db)):
    _auth(token or x_bridge_token)
    res = await broker_feed.ingest(db, body.symbol, body.timeframe, body.bars)
    if res.get("ok"):
        from services.realtime import on_bars
        background.add_task(on_bars, res["symbol"], res["timeframe"])
    return res


@router.post("/account")
async def account(snapshot: Dict, token: Optional[str] = Query(None),
                  x_bridge_token: Optional[str] = Header(None)):
    _auth(token or x_bridge_token)
    key = str(snapshot.get("login") or "default")
    _accounts[key] = {**snapshot, "received_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    return {"ok": True}


@router.get("/status")
async def status():
    try:
        feed = await broker_feed.status()
    except Exception as e:
        feed = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {"feed": feed, "accounts": list(_accounts.values())}
