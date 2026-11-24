"""Trade journal (every closed trade from the EA).

  GET /journal/stats?days=30&symbol=EURUSD   win / loss / breakeven stats + equity curve
  GET /journal/trades?limit=100              latest closed trades
  GET /journal/report?period=day|week|month&notify=true   Telegram summary
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Query

from services import trade_journal as tj

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("/stats")
async def stats(days: Optional[int] = Query(None, ge=1, le=3650), symbol: Optional[str] = Query(None)):
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days) if days else None
    return tj.summarise(await tj.rows_since(since, symbol))


@router.get("/trades")
async def trades(limit: int = Query(100, ge=1, le=1000)):
    rows = await tj.rows_since()
    return {"trades": [{**r, "close_time": r["close_time"].isoformat() if r["close_time"] else None}
                       for r in rows[-limit:][::-1]]}


@router.get("/report")
async def report(period: str = Query("day", pattern="^(day|week|month)$"), notify: bool = Query(False)):
    now = dt.datetime.now(dt.timezone.utc)
    since = {"day": now - dt.timedelta(days=1), "week": now - dt.timedelta(days=7),
             "month": now - dt.timedelta(days=30)}[period]
    s = tj.summarise(await tj.rows_since(since))
    out = {"period": period, **{k: v for k, v in s.items() if k != "equity_curve"}}
    if notify and s.get("trades"):
        from services.realtime import alert_once
        out["sent"] = await alert_once(f"journal:{period}:{now:%Y-%m-%d}", tj.report_card(period, s), ttl_hours=20)
    return out
