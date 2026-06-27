"""CBDR (Central Bank Dealers Range) API — multi-window.

  GET  /cbdr/windows                list the configured session windows
  GET  /cbdr/{symbol}               box + SD levels for the latest completed
                                    window, e.g. /cbdr/USD/JPY?window=prelondon
  GET  /cbdr/{symbol}?price=..      also returns the bias read at that price
  POST /cbdr/alert/{symbol}         push the box + SD levels to Telegram
                                    (the pre-London cron calls this at 03:00 UTC)

window defaults to "cbdr" (2pm–8pm NY). Use window=prelondon for the 18:00–02:45
UTC pre-London box. SD extensions are symmetric: lower SDs floor a buy, upper
SDs cap a sell, same multiples.
"""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from decouple import config

from cbdr.engine import build_cbdr, read_bias, nearest_levels, WINDOWS
from services.cbdr_service import fetch_cbdr_window
from utils.price_fetcher import get_forex_price

router = APIRouter(prefix="/cbdr", tags=["cbdr"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


async def _send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[cbdr] telegram not configured — skipping send")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={"chat_id": CHAT_ID, "text": text,
                                    "parse_mode": "Markdown"})
        return True
    except Exception as e:
        print(f"[cbdr] send failed: {e}")
        return False


async def _build(symbol: str, window: str, price: Optional[float]) -> dict:
    win = await fetch_cbdr_window(symbol, window)
    if not win:
        raise HTTPException(status_code=502,
                            detail=f"could not fetch CBDR window '{window}' for {symbol}")
    box = build_cbdr(win["high"], win["low"])
    out = {
        "symbol": symbol,
        "window": win["window"],
        "window_label": win["label"],
        "session": win["session"],
        "interval": win["interval"],
        "tz": win["tz"],
        "bars_used": win["bars"],
        "box": box.to_dict(),
    }
    if price is None:
        try:
            price = await get_forex_price(symbol)
        except Exception:
            price = None
    if price is not None:
        out["price"] = price
        out["bias"] = read_bias(price, box)
        out["nearest_levels"] = nearest_levels(price, box, n=3)
    return out


@router.get("/windows")
async def windows():
    return {k: {"label": w.label, "tz": w.tz, "interval": w.interval}
            for k, w in WINDOWS.items()}


@router.get("/{symbol:path}")
async def cbdr(symbol: str, window: str = Query("cbdr"),
               price: Optional[float] = Query(None)):
    return await _build(symbol, window, price)


@router.post("/alert/{symbol:path}")
async def cbdr_alert(symbol: str, window: str = Query("prelondon")):
    """Build the box and push its SD levels to Telegram (cron-friendly)."""
    out = await _build(symbol, window, None)
    box = out["box"]
    lvl = box["levels"]
    lines = [
        f"📦 *CBDR — {out['window_label']}*  ({symbol}, {out['session']})",
        "",
        f"High {box['high']}   Mid {round(box['mid'], 5)}   Low {box['low']}",
        f"Range {round(box['range'], 5)}  ·  {out['bars_used']} bars",
        "",
        "*Sell ceilings:*  " + "  ".join(f"{k} {lvl[k]}" for k in ("+1SD", "+2SD", "+3SD") if k in lvl),
        "*Buy floors:*     " + "  ".join(f"{k} {lvl[k]}" for k in ("-1SD", "-2SD", "-3SD") if k in lvl),
    ]
    if "bias" in out:
        lines += ["", f"_Now {out['price']} — {out['bias']['note']}_"]
    sent = await _send("\n".join(lines))
    return {"sent": sent, "symbol": symbol, "window": window, "session": out["session"]}
