"""USD/JPY forward-test API.

The endpoints mirror the workbook so the whole thing is "spreadsheet + a glance"
without the spreadsheet:

  POST /usdjpy/close          paste a daily close (manual) -> signal + sizing
  POST /usdjpy/scan           auto-fetch today's close from the feed and run it
  GET  /usdjpy/signal         latest evaluated row (today's BUY/SELL/NO TRADE)
  GET  /usdjpy/scoreboard     live tally + PASS/FAIL/INCONCLUSIVE verdict
  GET  /usdjpy/trades         trade journal (open + closed)
  GET  /usdjpy/closes         daily log
  GET  /usdjpy/risk           full risk/target table for all account sizes
  GET  /usdjpy/risk/{size}    sizing + take-profit targets for one account size
  GET  /usdjpy/rules          the frozen rules
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from services import usdjpy_service as svc
from services.usdjpy_close_service import fetch_daily_close
from services.usdjpy_alert import alert_signal
from usdjpy.engine import FROZEN_RULES
from usdjpy.risk_engine import (
    ACCOUNT_SIZES,
    risk_profile,
    risk_table,
    trade_money_risk,
)

router = APIRouter(prefix="/usdjpy", tags=["usdjpy"])


class CloseIn(BaseModel):
    close: float
    date: Optional[str] = None              # defaults to today (UTC)
    account_size: Optional[float] = None     # to attach sizing to the response
    notify: bool = False                     # send Telegram alert on a signal


def _attach_sizing(result: dict, account_size: Optional[float]) -> dict:
    sig = result.get("signal") or {}
    if account_size:
        result["risk"] = risk_profile(account_size).to_dict()
        if sig.get("is_trade") and sig.get("stop_pips"):
            result["trade_risk"] = trade_money_risk(
                account_size, sig["stop_pips"], sig["close"]
            )
    return result


@router.post("/close")
async def submit_close(payload: CloseIn, db: AsyncSession = Depends(get_db)):
    d = payload.date or date.today().isoformat()
    result = await svc.ingest_close(db, d, payload.close, source="manual")
    result = _attach_sizing(result, payload.account_size)
    if payload.notify and (result.get("signal") or {}).get("is_trade"):
        await alert_signal(result["signal"], result.get("trade_risk"))
    return result


@router.post("/scan")
async def scan(
    account_size: Optional[float] = Query(None),
    notify: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Auto-fetch the latest USD/JPY daily close from the feed and evaluate it."""
    fetched = await fetch_daily_close()
    if not fetched:
        raise HTTPException(status_code=502, detail="could not fetch USD/JPY close")
    d, close, source = fetched
    result = await svc.ingest_close(db, d, close, source=source)
    result["fetched"] = {"date": str(d), "close": close, "source": source}
    result = _attach_sizing(result, account_size)
    if notify and (result.get("signal") or {}).get("is_trade"):
        await alert_signal(result["signal"], result.get("trade_risk"))
    return result


@router.get("/signal")
async def signal(db: AsyncSession = Depends(get_db)):
    latest = await svc.latest_signal(db)
    if latest is None:
        return {"signal": None, "message": "no closes ingested yet"}
    return latest


@router.get("/scoreboard")
async def scoreboard(db: AsyncSession = Depends(get_db)):
    return await svc.get_scoreboard(db)


@router.get("/trades")
async def trades(limit: int = 200, db: AsyncSession = Depends(get_db)):
    return await svc.list_trades(db, limit)


@router.get("/closes")
async def closes(limit: int = 200, db: AsyncSession = Depends(get_db)):
    return await svc.list_closes(db, limit)


@router.get("/risk")
async def risk_all():
    return {"account_sizes": ACCOUNT_SIZES, "table": risk_table()}


@router.get("/risk/{account_size}")
async def risk_one(account_size: float):
    if account_size <= 0:
        raise HTTPException(status_code=400, detail="account_size must be positive")
    return risk_profile(account_size).to_dict()


@router.get("/rules")
async def rules():
    return FROZEN_RULES
