"""USD/JPY forward-test API.

The endpoints mirror the workbook so the whole thing is "spreadsheet + a glance"
without the spreadsheet:

  POST /usdjpy/close          paste a daily close (manual) -> signal + sizing
  POST /usdjpy/scan           auto-fetch today's close from the feed and run it
  POST /usdjpy/seed           warm-start from the built-in workbook closes
  POST /usdjpy/backfill       fill gaps from the feed's time_series (real closes)
  GET  /usdjpy/audit          inverse-rate of recent signals + NFP proximity
  GET  /usdjpy/signal         latest evaluated row (today's BUY/SELL/NO TRADE)
  GET  /usdjpy/scoreboard     live tally + PASS/FAIL/INCONCLUSIVE verdict
  GET  /usdjpy/trades         trade journal (open + closed)
  GET  /usdjpy/closes         daily log
  GET  /usdjpy/risk           full risk/target table for all account sizes
  GET  /usdjpy/risk/{size}    sizing + take-profit targets for one account size
  GET  /usdjpy/rules          the frozen rules
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from services import usdjpy_service as svc
from services.usdjpy_close_service import fetch_daily_close, fetch_daily_series
from services.usdjpy_alert import alert_signal
from services.news_guard import news_flag, nfp_window
from usdjpy.engine import FROZEN_RULES
from usdjpy.seed_from_workbook import WORKBOOK_CLOSES
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
    sig = result.get("signal") or {}
    if sig.get("is_trade"):
        sig["news_warning"] = await news_flag(date.fromisoformat(str(d)[:10]), "USD/JPY")
    if payload.notify and sig.get("is_trade"):
        await alert_signal(sig, result.get("trade_risk"))
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
    sig = result.get("signal") or {}
    if sig.get("is_trade"):
        d_obj = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        sig["news_warning"] = await news_flag(d_obj, "USD/JPY")
    if notify and sig.get("is_trade"):
        await alert_signal(sig, result.get("trade_risk"))
    return result


@router.post("/seed")
async def seed(db: AsyncSession = Depends(get_db)):
    """Warm-start the forward test with the built-in workbook closes (idempotent).

    Ingests the 25 DAILY-LOG closes from usdjpy/seed_from_workbook.py so the live
    system boots with the same ~20-day warm-up the workbook had — no Render Shell
    needed. Re-running is safe: ingest_close upserts by date.
    """
    for d, close in WORKBOOK_CLOSES:
        await svc.ingest_close(db, date.fromisoformat(d), close, source="workbook")
    return {
        "seeded": len(WORKBOOK_CLOSES),
        "latest_signal": await svc.latest_signal(db),
        "scoreboard": await svc.get_scoreboard(db),
    }


@router.post("/backfill")
async def backfill(
    days: int = Query(40, ge=1, le=500, description="how many recent daily bars to pull"),
    db: AsyncSession = Depends(get_db),
):
    """Backfill the daily log from the feed's time_series with REAL closes.

    Fixes calendar gaps (e.g. the month between the workbook's last close and the
    first live scan) so the MA20/SD20 window is contiguous. Idempotent — upserts
    by date and recomputes each row from its own ≤date window.
    """
    series = await fetch_daily_series(days)
    if not series:
        raise HTTPException(status_code=502,
                            detail="could not fetch USD/JPY time_series")
    for d, close, source in series:
        await svc.ingest_close(db, d, close, source=source)
    return {
        "requested_days": days,
        "ingested": len(series),
        "first": str(series[0][0]),
        "last": str(series[-1][0]),
        "latest_signal": await svc.latest_signal(db),
        "scoreboard": await svc.get_scoreboard(db),
    }


@router.get("/signal")
async def signal(db: AsyncSession = Depends(get_db)):
    latest = await svc.latest_signal(db)
    if latest is None:
        return {"signal": None, "message": "no closes ingested yet"}
    if latest.get("signal") in ("BUY", "SELL"):
        latest["news_warning"] = await news_flag(
            date.fromisoformat(str(latest["trade_date"])[:10]), "USD/JPY"
        )
    return latest


@router.get("/audit")
async def audit(days: int = Query(14, ge=1, le=365), db: AsyncSession = Depends(get_db)):
    """Audit fired signals over the last ``days``: inverse-rate + NFP proximity.

    A fade that closes negative is an 'inverse' trade (price continued against the
    fade). Tags which of those landed in an NFP jobs window to quantify how much
    of the damage is news-driven.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    trades = [t for t in await svc.list_trades(db, 500) if t["entry_date"] >= cutoff]

    rows, wins, inverse, inverse_nfp, open_n = [], 0, 0, 0, 0
    for t in trades:
        r = t.get("result_r")
        nfp = nfp_window(date.fromisoformat(t["entry_date"]))
        if r is None:
            outcome, open_n = "OPEN", open_n + 1
        elif r > 0:
            outcome, wins = "win", wins + 1
        else:
            outcome, inverse = "inverse", inverse + 1
            if nfp:
                inverse_nfp += 1
        rows.append({
            "entry_date": t["entry_date"], "action": t["action"],
            "entry": t["entry"], "exit_price": t.get("exit_price"),
            "result_r": r, "outcome": outcome,
            "stop_breached": t.get("stop_breached"), "nfp_window": nfp,
        })

    closed = wins + inverse
    return {
        "window_days": days, "signals": len(trades), "closed": closed,
        "open": open_n, "wins": wins, "inverse": inverse,
        "inverse_rate": round(inverse / closed, 3) if closed else None,
        "inverse_in_nfp_window": inverse_nfp,
        "note": ("Inverse trades clustered in NFP windows confirm news-driven "
                 "inversion — the flag-only news guard now warns on these."),
        "trades": rows,
    }


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
