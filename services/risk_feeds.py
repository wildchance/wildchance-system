"""Live risk feeds — VIX (market stress) and SPX (risk appetite).

Powers the VAULTUM market_stress + risk_appetite scores. Best-effort with a short
in-process TTL cache so the board and crons don't burn TwelveData quota. Every fetch
degrades to None on failure — the score layer treats None as neutral/low-confidence.
"""

from __future__ import annotations

import time
from typing import Optional, Dict

_CACHE: Dict[str, tuple] = {}          # key -> (value, expires_at)
_TTL = 300                              # 5-minute cache


def _cached(key: str):
    v = _CACHE.get(key)
    if v and v[1] > time.time():
        return v[0]
    return None


def _put(key: str, value):
    _CACHE[key] = (value, time.time() + _TTL)
    return value


async def vix_level() -> Optional[float]:
    """Current VIX. Tries TwelveData symbol variants; None if unavailable."""
    hit = _cached("vix")
    if hit is not None:
        return hit
    try:
        from utils.price_fetcher import get_latest_price
        for sym in ("VIX", "VIX.INDX", "^VIX"):
            try:
                p = await get_latest_price(sym)
            except Exception:
                p = None
            if p and p > 0:
                return _put("vix", float(p))
    except Exception:
        pass
    return None


async def spx_change_pct() -> Optional[float]:
    """S&P 500 session change %, from the last two daily closes. None if unavailable."""
    hit = _cached("spx_chg")
    if hit is not None:
        return hit
    try:
        from services.ohlc_service import fetch_ohlc
        for sym in ("SPX", "GSPC", "SPY", "US500"):
            try:
                bars = await fetch_ohlc(sym, "1day", 2)
            except Exception:
                bars = None
            if bars and len(bars) >= 2:
                prev_close = float(bars[-2][4])
                last_close = float(bars[-1][4])
                if prev_close:
                    return _put("spx_chg", round((last_close - prev_close) / prev_close * 100, 2))
    except Exception:
        pass
    return None


async def risk_feeds() -> dict:
    """Both feeds in one call, for the VAULTUM board."""
    vix = await vix_level()
    spx = await spx_change_pct()
    state = None
    if spx is not None or vix is not None:
        # crude risk state: sharp equity drop or spiking VIX = risk-off
        if (spx is not None and spx <= -0.8) or (vix is not None and vix >= 22):
            state = "risk_off"
        elif (spx is not None and spx >= 0.6) and (vix is None or vix < 18):
            state = "risk_on"
        else:
            state = "neutral"
    return {"vix": vix, "spx_change_pct": spx, "risk_state": state}
