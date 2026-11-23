"""Trade-quality gate — the owner's rules applied before ANY new-trade alert.

  SESSION   no new trades while the CBDR box is still forming (14:00–20:00 New York)
            nor in the thin first hour after the Sunday open (17:00–18:00 NY)
  STOP      at least the owner's 10-pip FX stop (sheet: 100 points); gold ≥ $3
            (MIN_SL_PIPS / MIN_SL_GOLD_USD / MIN_SL_BTC_USD) — no 1.7-pip noise stops
  R:R       the furthest target must pay ≥ MIN_RR (1.3R = the first sheet rung)
  WEEKLY    the ICT weekly profile (Monday-sweep bias, gold.weekly) must confirm
            the side; "diverges" blocks, "neutral" is not a confirmation. While the
            week is still inside Monday's range, last week's profile carries over
            (WEEKLY_PRIOR_FALLBACK, default on) and is labelled prior_week
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from decouple import config

NY = ZoneInfo("America/New_York")
MIN_RR = float(config("MIN_RR", default="1.3"))
MIN_SL_PIPS = float(config("MIN_SL_PIPS", default="10"))
MIN_SL_GOLD_USD = float(config("MIN_SL_GOLD_USD", default="3.0"))
# BTC mirrors gold's structure: a fixed USD floor. Gold's $3 ≈ 0.075 % of ~$4,000;
# the same share of ~$60k BTC ≈ $45 → default $50.
MIN_SL_BTC_USD = float(config("MIN_SL_BTC_USD", default="50"))
WEEKLY_PRIOR_FALLBACK = str(config("WEEKLY_PRIOR_FALLBACK", default="true")).lower() in ("1", "true", "yes", "on")
BLOCK_CBDR_FORMING = str(config("BLOCK_CBDR_FORMING", default="true")).lower() in ("1", "true", "yes", "on")


def session_block(now: Optional[dt.datetime] = None) -> Optional[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    from services.freshness import market_open
    if not market_open(now):            # weekend: bars are Friday's — never a live entry
        return "market closed (Fri 17:00 → Sun 17:00 NY) — setups would use Friday's bars"
    ny = now.astimezone(NY)
    if BLOCK_CBDR_FORMING and 14 <= ny.hour < 20:
        return "CBDR box forming (14:00–20:00 NY) — wait for the box"
    if ny.weekday() == 6 and 17 <= ny.hour < 18:
        return "thin Sunday open (17:00–18:00 NY)"
    return None


def min_stop_distance(symbol: str) -> float:
    s = symbol.upper().replace("/", "")
    if s.startswith(("XAU", "GOLD")):
        return MIN_SL_GOLD_USD
    if s.startswith(("BTC", "XBT")):
        return MIN_SL_BTC_USD
    if len(s) >= 6 and s[3:6] == "JPY":
        return MIN_SL_PIPS * 0.01
    if len(s) >= 6 and s[:6].isalpha():
        return MIN_SL_PIPS * 0.0001
    return 0.0


def widen_stop(symbol: str, side: str, entry: float, stop: float) -> float:
    """Push the stop out to the minimum distance (never pull it in)."""
    need = min_stop_distance(symbol)
    if abs(entry - stop) >= need:
        return stop
    return round(entry - need if side.upper() in ("BUY", "LONG") else entry + need, 5)


def best_rr(entry: float, stop: float, targets: List[float]) -> Optional[float]:
    risk = abs(entry - stop)
    if risk <= 0 or not targets:
        return None
    return round(max(abs(t - entry) for t in targets if t is not None) / risk, 2)


_wcache: Dict[str, Dict] = {}


async def weekly_profile(symbol: str) -> Optional[Dict]:
    """ICT weekly profile from daily bars (cached 1h; daily bars cached 12h upstream)."""
    hit = _wcache.get(symbol)
    if hit and time.time() - hit["_t"] < 3600:
        return hit["read"]
    try:
        from gold.weekly import weekly_bias
        from services.ohlc_service import fetch_ohlc
        read = weekly_bias(await fetch_ohlc(symbol, "1day", 15), prior_fallback=WEEKLY_PRIOR_FALLBACK)
    except Exception:
        read = None
    _wcache[symbol] = {"read": read, "_t": time.time()}
    return read


def weekly_verdict(read: Optional[Dict], side: str) -> str:
    from gold.weekly import confluence
    return confluence(read, side)


def evaluate(symbol: str, side: str, entry: float, stop: float, targets: List[float],
             weekly: Optional[Dict] = None, now: Optional[dt.datetime] = None) -> Dict:
    """Pure verdict. ``weekly`` = gold.weekly.weekly_bias(...) result (or None)."""
    reasons = []
    sb = session_block(now)
    if sb:
        reasons.append(sb)
    rr = best_rr(entry, stop, targets)
    if rr is None or rr < MIN_RR:
        reasons.append(f"R:R {rr} below {MIN_RR}R (first rung)")
    wv = weekly_verdict(weekly, side)
    if wv == "diverges":
        reasons.append(f"weekly profile is {weekly.get('bias')} ({weekly.get('profile')})")
    return {"allow": not reasons, "reasons": reasons, "best_rr": rr, "weekly": wv}
