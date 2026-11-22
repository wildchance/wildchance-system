"""Composite MARKET SENTIMENT per asset — AI news + Polymarket + Myfxbook retail.

  news        ai_sentiment (LLM on Google News headlines)          weight 0.4
  polymarket  prediction-market probabilities → asset direction    weight 0.3
  retail      Myfxbook Community Outlook, read CONTRARIAN           weight 0.3
              (70 % of retail long ⇒ −0.67: the crowd is usually wrong at extremes)

composite = weighted mean of the components that are available (−1 … +1).
Auto macro lock (SENTIMENT_AUTO_BIAS=true): |composite| ≥ 0.5 AND at least two
components agree with its sign. Manual MACRO_BIAS still always wins.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

WEIGHTS = {"news": 0.4, "polymarket": 0.3, "retail": 0.3}
LOCK_AT = 0.5


def retail_contrarian(long_pct: float) -> float:
    return round(max(-1.0, min(1.0, (50.0 - float(long_pct)) / 30.0)), 2)


def combine(components: Dict[str, Optional[float]]) -> Dict:
    have = {k: v for k, v in components.items() if v is not None}
    if not have:
        return {"composite": None, "components": components, "lock": None, "agree": 0}
    w = sum(WEIGHTS[k] for k in have)
    comp = round(sum(WEIGHTS[k] * v for k, v in have.items()) / w, 2)
    agree = sum(1 for v in have.values() if v * comp > 0 and abs(v) >= 0.15)
    lock = None
    if abs(comp) >= LOCK_AT and agree >= 2:
        lock = "BUY_ONLY" if comp > 0 else "SELL_ONLY"
    return {"composite": comp, "components": components, "agree": agree, "lock": lock,
            "bias": "BULLISH" if comp >= 0.2 else "BEARISH" if comp <= -0.2 else "NEUTRAL"}


def components_for(symbol: str) -> Dict[str, Optional[float]]:
    """Read every source from its CACHE only (no network) — safe on the hot path."""
    sym = symbol.upper().replace("/", "")
    out: Dict[str, Optional[float]] = {"news": None, "polymarket": None, "retail": None}
    try:
        from services import ai_sentiment as ai
        s = ai._cache.get(sym)
        if s and time.time() - s["_t"] < ai.TTL_MIN * 60 and s.get("confidence", 0) >= 0.3:
            out["news"] = s["score"]
    except Exception:
        pass
    try:
        from services import polymarket_service as pm
        a = ((pm._cache.get("data") or {}).get("assets") or {}).get(sym)
        if a and a["markets"] >= 2 and time.time() - pm._cache["t"] < 6 * 3600:
            out["polymarket"] = a["score"]
    except Exception:
        pass
    try:
        from wildchance import wildchance_scraper as wc
        data = wc._retail_cache.get("data") or {}
        r = data.get(f"{sym[:3]}/{sym[3:]}")
        if r and not r.get("stale") and time.time() - wc._retail_cache["t"] < 24 * 3600:
            out["retail"] = retail_contrarian(r["long_pct"])
    except Exception:
        pass
    return out


def read(symbol: str) -> Dict:
    return {"symbol": symbol.upper().replace("/", ""), **combine(components_for(symbol))}


def cached_locks(symbols) -> Dict[str, str]:
    from services.ai_sentiment import AUTO_BIAS
    if not AUTO_BIAS:
        return {}
    out = {}
    for s in symbols:
        r = read(s)
        if r["lock"]:
            out[r["symbol"]] = r["lock"]
    return out
