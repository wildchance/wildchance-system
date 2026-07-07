"""Reference-candle range endpoints..."""
from __future__ import annotations
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from decouple import config
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from services import candlerange_service as crs
from services import gold_positions as gp
from services import trade_executor as te
from gold.limit_orders import size_limit

router = APIRouter(prefix="/candlerange", tags=["candlerange"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)

DEFAULT_WATCH = ["USD/JPY", "EUR/USD", "GBP/USD", "XAU/USD", "NAS100"]


async def _send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
        return True
    except Exception:
        return False


@router.get("/{symbol:path}")
async def candlerange(symbol: str, price: Optional[float] = Query(None)):
    result = await crs.analyze_symbol(symbol, price)
    if result is None:
        raise HTTPException(status_code=502, detail=f"no candle data for {symbol}")
    return result


@router.post("/crt")
async def crt(symbols: str = Query(None),
              session: str = Query(None, description="asia | ny (default: by UTC hour)"),
              balance: float = Query(5000, gt=0),
              risk_usd: float = Query(20.0, gt=0),
              track: bool = Query(True, description="open a tracked gold position on a confirmed XAU/USD CRT"),
              execute: bool = Query(False, description="also enqueue an MT5 order on a confirmed CRT"),
              force: bool = Query(False),
              db: AsyncSession = Depends(get_db)):
    """1-5-9 CRT scan: arm the session's limit ladder + check 5-o'clock confirmation.

    On a CONFIRMED XAU/USD trigger, the confirmed limit is money-sized and opened
    as a tracked position (monitored to TP/SL). Call at 05:00 (Asian) / 17:00 (NY)."""
    watch = ([s.strip() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_WATCH)
    reads, alerts, opened = [], [], []
    for sym in watch:
        r = await crs.crt_read(sym, session=session)
        if r is None:
            continue
        reads.append(r)
        text = crs.format_crt_alert(r)
        if text:
            alerts.append(text)
        conf = (r.get("confirmation") or {})
        if track and sym.upper().replace("-", "/") == "XAU/USD" and conf.get("confirmed"):
            card = size_limit(conf["order"], balance, risk_usd)   # confirmed = fills now
            pos = await gp.open_from_signal(db, card, source="gold_crt")
            if pos:
                rec = {"tracked": pos}
                if execute and card.get("signal") in ("LONG", "SHORT"):
                    order = te.build_order(card, source="gold_crt")
                    if order:
                        rec["queued_order"] = await te.enqueue(db, order)
                opened.append(rec)
    sent = False
    if alerts:
        sent = await _send("\n\n".join(alerts))
    elif force:
        sent = await _send("🎯 *CRT 1-5-9* — no confirmations at the limits right now.")
    return {"scanned": len(watch), "reads": reads, "opened": opened, "sent": sent}


@router.post("/friday")
async def friday(symbols: str = Query(None), force: bool = Query(False)):
    """Friday pre-close 1h read → Monday-gap anticipation (call ~20:55 UTC Fri)."""
    watch = ([s.strip() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_WATCH)
    reads, alerts = [], []
    for sym in watch:
        r = await crs.friday_close_read(sym)
        if r is None:
            continue
        reads.append(r)
        text = crs.format_friday_alert(r)
        if text:
            alerts.append(text)
    sent = False
    if alerts:
        sent = await _send("\n\n".join(alerts))
    elif force:
        sent = await _send("📅 *Monday-gap read* — Friday closes look flat / no clear gap.")
    return {"scanned": len(watch), "reads": reads, "sent": sent}


@router.post("/scan")
async def scan(symbols: str = Query(None), force: bool = Query(False)):
    watch = ([s.strip() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_WATCH)
    hits, alerts = [], []
    for sym in watch:
        r = await crs.analyze_symbol(sym)
        if r is None:
            continue
        text = crs.format_alert(r)
        if text:
            hits.append({"symbol": sym, "result": r})
            alerts.append(text)
    sent = False
    if alerts:
        sent = await _send("\n\n".join(alerts))
    elif force:
        sent = await _send("🕯️ *Candle-range scan* — nothing actionable right now.")
    return {"scanned": len(watch), "signals": len(hits), "sent": sent}
