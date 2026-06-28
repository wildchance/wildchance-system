"""Yearly Quarterly-Theory CBDR endpoints."""
from __future__ import annotations
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException, Query
from decouple import config
from services import quarterly_service as qsvc

router = APIRouter(prefix="/quarterly", tags=["quarterly"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


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
async def quarterly(symbol: str, price: Optional[float] = Query(None)):
    read = await qsvc.build_yearly(symbol, price)
    if read is None:
        raise HTTPException(status_code=502, detail=f"no daily data for {symbol}")
    return read


@router.get("/{symbol:path}/lookback")
async def lookback(symbol: str, price: Optional[float] = Query(None)):
    read = await qsvc.build_yearly(symbol, price)
    if read is None:
        raise HTTPException(status_code=502, detail=f"no daily data for {symbol}")
    return {"symbol": symbol, "price": read["price"], "lookback": read["lookback"]}


@router.post("/alert/{symbol:path}")
async def quarterly_alert(symbol: str):
    read = await qsvc.build_yearly(symbol)
    if read is None:
        raise HTTPException(status_code=502, detail=f"no daily data for {symbol}")
    sent = await _send(qsvc.format_alert(read))
    return {"sent": sent, "symbol": symbol, "phase": read["phase"], "year": read["year"]}
