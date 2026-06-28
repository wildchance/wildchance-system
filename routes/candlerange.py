"""Reference-candle range endpoints..."""
from __future__ import annotations
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException, Query
from decouple import config
from services import candlerange_service as crs

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
