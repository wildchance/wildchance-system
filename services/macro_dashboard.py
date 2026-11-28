"""FRED macro dashboard + regime tags + an AI brief (NVIDIA LLM writes, FRED supplies data).

FRED is free (your FRED_KEY); NVIDIA can't fetch data — it SUMMARISES what FRED returns.
Series (all official, St. Louis Fed):
  T10Y2Y  10y − 2y spread        T10Y3M  10y − 3m spread     DFF   fed funds
  DGS10   10y yield              CPIAUCSL CPI (→ YoY)         UNRATE unemployment
  ICSA    initial claims          VIXCLS  VIX                 BAMLH0A0HYM2 high-yield spread
  DTWEXBGS broad dollar index     WALCL   Fed balance sheet   T5YIE 5y breakeven inflation
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional

SERIES = {
    "T10Y2Y": ("10y-2y spread", 30), "T10Y3M": ("10y-3m spread", 30), "DFF": ("Fed funds", 10),
    "DGS10": ("10y yield", 30), "CPIAUCSL": ("CPI index", 13), "UNRATE": ("Unemployment", 6),
    "ICSA": ("Initial claims", 8), "VIXCLS": ("VIX", 20), "BAMLH0A0HYM2": ("HY spread", 30),
    "DTWEXBGS": ("Broad USD", 30), "WALCL": ("Fed balance sheet", 8), "T5YIE": ("5y breakeven", 30),
}
_cache: Dict = {"t": 0.0, "data": None, "brief": None, "brief_t": 0.0}


def curve_state(spread: Optional[float], history: List[float]) -> Optional[Dict]:
    if spread is None:
        return None
    was_inverted = any(v < 0 for v in history)
    state = "inverted" if spread < 0 else "flat" if spread < 0.25 else "positive"
    note = ("re-steepened after an inversion — historically the late-cycle phase where "
            "recessions have tended to begin (a warning, not a timing tool)"
            if state != "inverted" and was_inverted else None)
    return {"spread": spread, "state": state, "note": note}


def regime(d: Dict) -> Dict:
    """Plain tags from the latest values (thresholds are conventional, not signals)."""
    tags, risk_off = [], 0
    v = lambda k: (d.get(k) or {}).get("latest")
    if v("VIXCLS") is not None and v("VIXCLS") >= 25:
        tags.append("VIX ≥ 25: stressed"); risk_off += 1
    if v("BAMLH0A0HYM2") is not None and v("BAMLH0A0HYM2") >= 4.5:
        tags.append("HY spread ≥ 4.5%: credit stress"); risk_off += 1
    hy = (d.get("BAMLH0A0HYM2") or {}).get("change")
    if hy is not None and hy >= 0.5:
        tags.append("HY spread widening fast"); risk_off += 1
    cl = (d.get("ICSA") or {}).get("direction")
    if cl == "rising":
        tags.append("jobless claims rising")
    yoy = (d.get("CPIAUCSL") or {}).get("yoy_pct")
    if yoy is not None:
        tags.append(f"CPI {yoy:.1f}% YoY")
    usd = (d.get("DTWEXBGS") or {}).get("direction")
    if usd:
        tags.append(f"broad USD {usd}")
    return {"risk_off_score": risk_off,
            "posture": "RISK-OFF" if risk_off >= 2 else "CAUTIOUS" if risk_off == 1 else "NORMAL",
            "tags": tags}


async def dashboard(force: bool = False) -> Dict:
    if _cache["data"] and not force and time.time() - _cache["t"] < 6 * 3600:
        return _cache["data"]
    from services.fred_service import _series, configured, direction
    if not configured():
        return {"ok": False, "reason": "set FRED_KEY (free at fred.stlouisfed.org)"}
    vals = await asyncio.gather(*(_series(k, n) for k, (_, n) in SERIES.items()))
    d: Dict[str, Dict] = {}
    for (k, (label, _)), xs in zip(SERIES.items(), vals):
        if not xs:
            d[k] = {"label": label, "latest": None}
            continue
        row = {"label": label, "latest": round(xs[-1], 3), "direction": direction(xs[-5:]),
               "change": round(xs[-1] - xs[0], 3)}
        if k == "CPIAUCSL" and len(xs) >= 13:
            row["yoy_pct"] = round(100 * (xs[-1] / xs[-13] - 1), 2)
        d[k] = row
    out = {"ok": True, "series": d,
           "yield_curve": {"10y_2y": curve_state((d["T10Y2Y"] or {}).get("latest"),
                                                  [x for x in (vals[0] or [])]),
                           "10y_3m": curve_state((d["T10Y3M"] or {}).get("latest"),
                                                  [x for x in (vals[1] or [])])},
           "regime": regime(d), "as_of": time.strftime("%Y-%m-%d", time.gmtime())}
    _cache.update(t=time.time(), data=out)
    return out


async def brief(force: bool = False) -> Dict:
    """5-line AI macro brief (NVIDIA). The model only rewrites FRED's numbers."""
    if _cache["brief"] and not force and time.time() - _cache["brief_t"] < 6 * 3600:
        return _cache["brief"]
    dash = await dashboard()
    if not dash.get("ok"):
        return dash
    from services import ai_sentiment as ai
    import json
    msgs = [{"role": "system", "content":
             "You are a macro strategist. Using ONLY the JSON data given (no outside facts), "
             "write 5 short bullets: yield curve, inflation, labour, credit/volatility, and "
             "the implication for gold, the US dollar and US indices. Say 'data missing' "
             "where a series is null. No predictions with dates or price targets."},
            {"role": "user", "content": json.dumps({"series": dash["series"],
                                                    "yield_curve": dash["yield_curve"],
                                                    "regime": dash["regime"]})}]
    txt = await ai.ask_model(msgs, role="deep")
    out = {"as_of": dash["as_of"], "posture": dash["regime"]["posture"],
           "brief": txt or "AI brief is temporarily unavailable — the macro numbers below are live.",
           "model": ai.LAST.get("model") if txt else None}
    if txt:
        _cache.update(brief=out, brief_t=time.time())
    return out
