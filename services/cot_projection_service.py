"""COT projection (B7) — bridge the 3-5 day official-COT reporting lag.

The CFTC COT is published Fridays for the prior Tuesday, so it is always 3-5 days
stale. This projects the non-commercial net FORWARD from the report date using the
signals that lead positioning: price action since the report (specs chase trend),
the week-over-week momentum, and the options-flow lean when fed. It never replaces
the official print — it estimates where positioning likely sits NOW.
"""

from __future__ import annotations

import datetime as _dt

# Rough sensitivity: spec net contracts added per 1% gold move over the stale window.
_SENSITIVITY = 4000.0
_WOW_MOMENTUM = 0.3
_OPTIONS_TILT = 1500.0
_ETF_TILT = 1200.0


async def project_cot(symbol: str = "XAU/USD") -> dict:
    from services.cftc_service import gold_cot
    cot = await gold_cot()
    if not cot:
        return {"ok": False, "reason": "no official COT available"}
    net = cot["noncomm_net"]
    wow = cot.get("wow_net_change")
    rep = cot.get("report_date") or ""

    days = None
    try:
        days = (_dt.date.today() - _dt.date.fromisoformat(rep)).days
    except Exception:
        pass

    price_change_pct = None
    try:
        from services.ohlc_service import fetch_ohlc
        daily = await fetch_ohlc(symbol, "1day", 20)
        closes = [r[4] for r in daily]
        if days and days > 0 and len(closes) > days:
            past = closes[-1 - days]
            price_change_pct = (closes[-1] - past) / past if past else 0.0
        elif len(closes) >= 2 and closes[-2]:
            price_change_pct = (closes[-1] - closes[-2]) / closes[-2]
    except Exception:
        pass

    opt_lean = 0
    try:
        from gold import options_flow as of
        if of.configured():
            opt_lean = {"bullish": 1, "bearish": -1}.get(of.flow_bias().get("bias"), 0)
    except Exception:
        pass

    # ETF flow lean — physical/paper accumulation leads spec positioning.
    etf_lean = 0
    try:
        from gold import macro_cycle as mc
        etf_lean = {"accumulation": 1, "easing_outflows": 0,
                    "outflows": -1}.get(mc.INPUTS.get("etf_flow_direction"), 0)
    except Exception:
        pass

    drift = 0.0
    if price_change_pct is not None:
        drift += price_change_pct * _SENSITIVITY
    if wow:
        drift += _WOW_MOMENTUM * wow
    drift += opt_lean * _OPTIONS_TILT
    drift += etf_lean * _ETF_TILT
    projected = int(net + drift)

    return {
        "ok": True, "symbol": symbol,
        "official_net": net, "report_date": rep, "days_stale": days,
        "wow_net_change": wow,
        "price_change_since_report_pct": (round(price_change_pct * 100, 2)
                                          if price_change_pct is not None else None),
        "options_lean": opt_lean, "etf_lean": etf_lean,
        "projected_net": projected, "projected_drift": int(drift),
        "direction": ("adding longs" if drift > 0 else
                      "cutting longs" if drift < 0 else "flat"),
        "note": (f"official net {net} ({days}d stale) → projected {projected} "
                 f"({'+' if drift >= 0 else ''}{int(drift)} from price/options flow)"),
    }
