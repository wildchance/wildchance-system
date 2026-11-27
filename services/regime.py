"""Market REGIME per symbol (research layer E) + volatility-scaled RISK BUDGET (layer D).

REGIME (daily bars, cached 1 h)
  efficiency ratio ER = |close − close 20 days ago| / Σ|daily moves|   (0 = chop, 1 = straight line)
  ATR percentile  = today's ATR(14) against the last ~250 days
  EVENT    a high-impact release for the pair's currencies within 3 h
  STRESSED VIX ≥ 30 or ATR in its top 10 %
  TRENDING ER ≥ REGIME_TREND_ER (0.35) — direction = sign of the 20-day move
  RANGING  otherwise
  Engine routing: mean-REVERSION setups (Z fades, swing fades, Asia sweeps) are blocked when they
  fade a TRENDING market's direction; CONTINUATION setups (auto CBDR continuation, Asia breakouts)
  are blocked in a RANGING market. Stressed / event regimes only shrink size (risk budget).

RISK BUDGET (risk % per trade = RISK_BASE_PCT 0.5 × factors, never above the base)
  ATR percentile ≥ 80 → ×0.6 · ≥ 60 → ×0.8   VIX ≥ 35 → ×0.5 · ≥ 25 → ×0.75
  event within 24 h → ×0.7                      ≥ 4 open tracked signals → ×0.5 · ≥ 2 → ×0.75
  floor RISK_MIN_PCT (0.15). Lot ladders on every card use it (propfirm.target_lots.card_lines).
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from decouple import config

TREND_ER = float(config("REGIME_TREND_ER", default="0.35"))
RISK_BASE = float(config("RISK_BASE_PCT", default="0.5"))
RISK_MIN = float(config("RISK_MIN_PCT", default="0.15"))
_cache: Dict[str, Tuple[float, Dict]] = {}
_budget: Dict[str, Tuple[float, Dict]] = {}


def efficiency(closes: List[float], n: int = 20) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - n, len(closes)))
    return (closes[-1] - closes[-1 - n]) / path if path else 0.0


def atr_series(bars: List[tuple], n: int = 14) -> List[float]:
    trs = [max(h - l, abs(h - bars[i - 1][4]), abs(l - bars[i - 1][4])) for i, (_d, _o, h, l, _c) in enumerate(bars) if i]
    return [sum(trs[i - n + 1:i + 1]) / n for i in range(n - 1, len(trs))]


def classify(er: Optional[float], atr_pct: Optional[float], vix: Optional[float], event: bool) -> Dict:
    if er is None and atr_pct is None and not event and (vix or 0) < 30:
        return {"regime": "UNKNOWN", "trend": None, "er": None, "atr_pct": None, "vix": vix, "event": False}
    if event:
        name = "EVENT"
    elif (vix or 0) >= 30 or (atr_pct or 0) >= 90:
        name = "STRESSED"
    elif er is not None and abs(er) >= TREND_ER:
        name = "TRENDING"
    else:
        name = "RANGING"
    trend = ("UP" if er > 0 else "DOWN") if er is not None and abs(er) >= TREND_ER else None
    return {"regime": name, "trend": trend, "er": round(er, 2) if er is not None else None,
            "atr_pct": atr_pct, "vix": vix, "event": event}


def route(reg: Dict, style: Optional[str], side: str) -> Optional[str]:
    """Reason to block an engine style in this regime, or None."""
    if not style:
        return None
    if style == "reversion" and reg.get("trend"):
        against = (reg["trend"] == "UP" and side == "SELL") or (reg["trend"] == "DOWN" and side == "BUY")
        if against:
            return f"regime TRENDING {reg['trend']} (ER {reg['er']}) — not fading a trend"
    if style == "continuation" and reg.get("regime") == "RANGING":
        return f"regime RANGING (ER {reg['er']}) — breakouts fail in ranges"
    return None


def budget(reg: Dict, open_signals: int, event_24h: bool, base: float = RISK_BASE) -> Dict:
    f, why = 1.0, []
    ap, vix = reg.get("atr_pct"), reg.get("vix")
    if ap is not None and ap >= 80:
        f, _ = f * 0.6, why.append(f"ATR {ap}th pct ×0.6")
    elif ap is not None and ap >= 60:
        f, _ = f * 0.8, why.append(f"ATR {ap}th pct ×0.8")
    if vix is not None and vix >= 35:
        f, _ = f * 0.5, why.append(f"VIX {vix} ×0.5")
    elif vix is not None and vix >= 25:
        f, _ = f * 0.75, why.append(f"VIX {vix} ×0.75")
    if event_24h:
        f, _ = f * 0.7, why.append("event < 24 h ×0.7")
    if open_signals >= 4:
        f, _ = f * 0.5, why.append(f"{open_signals} open ×0.5")
    elif open_signals >= 2:
        f, _ = f * 0.75, why.append(f"{open_signals} open ×0.75")
    pct = round(max(RISK_MIN, min(base, base * f)), 2)
    return {"risk_pct": pct, "factor": round(f, 2), "why": why}


async def compute(symbol: str, max_age_s: int = 3600) -> Dict:
    key = symbol.upper().replace("/", "")
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < max_age_s:
        return hit[1]
    er = ap = vix = adr_pips = None
    try:
        from services.ohlc_service import fetch_ohlc
        d = await fetch_ohlc(symbol, "1day", 300)
        if len(d or []) < 64:                     # gold with no recent spot reference: ratios only need the proxy
            from services.price_pool import raw_proxy_daily
            d = await raw_proxy_daily(symbol, 300) or d
        closes = [b[4] for b in d or []]
        er = efficiency(closes)
        atrs = atr_series(d or [])
        if len(atrs) >= 50:
            ap = int(100 * sum(1 for a in atrs[-250:] if a <= atrs[-1]) / len(atrs[-250:]))
        last = (d or [])[-20:]
        if len(last) >= 10:
            from propfirm.target_lots import pip_size
            pip = pip_size(symbol) or 0.0001
            adr_pips = round(sum(b[2] - b[3] for b in last) / len(last) / pip, 1)
    except Exception as e:
        from services import errors
        errors.swallow(f"regime.compute {symbol}", e)
    try:
        from services.data_feeds import macro_cached
        vix = (macro_cached().get("vix") or {}).get("close")
    except Exception:
        pass
    event = False
    try:
        import datetime as dt
        from services import econ_calendar as ec
        event = ec.blackout(symbol, dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=90)) is not None \
            or ec.blackout(symbol) is not None
    except Exception:
        pass
    reg = classify(er, ap, vix, event)
    reg["adr_pips"] = adr_pips
    _cache[key] = (time.time(), reg)
    return reg


async def risk_for(symbol: str) -> Dict:
    """Risk budget for a new card on ``symbol``; cached for the lot ladders (card_lines)."""
    reg = await compute(symbol)
    n_open = 0
    try:
        from services import signal_tracker
        n_open = sum(1 for r in await signal_tracker._load() if r["result"] == "open")
    except Exception:
        pass
    ev24 = False
    try:
        from services.zconfluence import event_soon
        ev24 = event_soon(symbol)
    except Exception:
        pass
    b = {**budget(reg, n_open, ev24), "regime": reg}
    _budget[symbol.upper().replace("/", "")] = (time.time(), b)
    return b


def cached_risk_pct(symbol: str, default: float = RISK_BASE, max_age_s: int = 6 * 3600) -> float:
    hit = _budget.get(symbol.upper().replace("/", ""))
    return hit[1]["risk_pct"] if hit and time.time() - hit[0] < max_age_s else default


def line(b: Dict) -> str:
    reg = b.get("regime") or {}
    r = f"🌡 Regime {reg.get('regime', '?')}" + (f" {reg['trend']}" if reg.get("trend") else "")
    return r + f" · ⚖️ risk {b['risk_pct']}%" + (f" ({', '.join(b['why'])})" if b.get("why") else "")
