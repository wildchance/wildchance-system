"""Swing mean-reversion scanner — HTF Bollinger ±1σ/±2σ extremes + CBDR confluence.

Universe: gold, silver, BTC, WTI, US indices and the FX majors/crosses (override with
SWING_UNIVERSE, comma-separated TwelveData symbols). Bars come from the EA broker
feed first (free, real-time) and TwelveData otherwise — see services/ohlc_service.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from decouple import config

from indicators import bollinger as bb

DEFAULT_UNIVERSE = ["XAU/USD", "XAG/USD", "BTC/USD", "WTI/USD", "SPX", "NDX", "DJI",
                    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "USD/CHF",
                    "NZD/USD", "GBP/JPY", "EUR/JPY"]
DISPLAY = {"XAU/USD": "XAUUSD", "XAG/USD": "XAGUSD", "BTC/USD": "BTCUSD", "WTI/USD": "USOIL",
           "SPX": "US500", "NDX": "NAS100", "DJI": "US30"}


# Owner's macro views as switchable Stage-0 presets (MACRO_REGIMES="a,b"). These are
# the OWNER's theses, not forecasts made by the system. Precedence (low → high):
# AI sentiment locks < regime presets < manual MACRO_BIAS.
REGIME_PRESETS: Dict[str, Dict[str, str]] = {
    # midterm "blue sweep" → policy uncertainty: risk-off tilt
    "political_uncertainty": {"US500": "SELL_ONLY", "NAS100": "SELL_ONLY", "US30": "SELL_ONLY",
                              "XAUUSD": "BUY_ONLY"},
    # Trump-1 analog on DXY: 2017-18 drop then 2018-19 rebound → USD-bullish leg
    "usd_bull_trump1_analog": {"EURUSD": "SELL_ONLY", "GBPUSD": "SELL_ONLY",
                               "AUDUSD": "SELL_ONLY", "NZDUSD": "SELL_ONLY",
                               "USDJPY": "BUY_ONLY", "USDCAD": "BUY_ONLY", "USDCHF": "BUY_ONLY"},
}


def active_regimes() -> List[str]:
    raw = str(config("MACRO_REGIMES", default="") or "")
    return [r.strip() for r in raw.split(",") if r.strip() in REGIME_PRESETS]


def macro_bias() -> Dict[str, str]:
    """Stage-0 weekly direction locks from your own HTF analysis (COT, currency meter,
    Elliott-wave maps…). Env MACRO_BIAS='{"BTCUSD":"SELL_ONLY","XAUUSD":"BOTH"}'."""
    import json
    out: Dict[str, str] = {}
    try:                                    # composite sentiment locks (opt-in; news +
        from services.market_sentiment import cached_locks   # Polymarket + retail agree)
        out.update(cached_locks([display(s) for s in universe()]))
    except Exception:
        pass
    for r in active_regimes():              # owner's regime presets
        out.update(REGIME_PRESETS[r])
    try:                                    # your manual locks always win
        raw = json.loads(config("MACRO_BIAS", default="{}") or "{}")
        out.update({str(k).upper().replace("/", ""): str(v).upper() for k, v in raw.items()})
    except Exception:
        pass
    return out


def apply_bias(row: Dict, bias: Optional[str]) -> Dict:
    """A fade against the macro lock is demoted to WATCH with a blocker (never deleted —
    you still see the extreme, you just don't trade it)."""
    if not bias or bias == "BOTH" or row.get("side") not in ("BUY", "SELL"):
        return row
    row = dict(row, macro_bias=bias)
    against = (bias == "SELL_ONLY" and row["side"] == "BUY") or \
              (bias == "BUY_ONLY" and row["side"] == "SELL")
    if against:
        row["blockers"] = list(row.get("blockers", [])) + [f"against macro bias {bias}"]
        if row.get("status") in ("ENTRY", "LIMIT"):
            row["status"] = "WATCH"
    return row


def universe() -> List[str]:
    raw = config("SWING_UNIVERSE", default="")
    return [s.strip() for s in raw.split(",") if s.strip()] or list(DEFAULT_UNIVERSE)


def display(symbol: str) -> str:
    return DISPLAY.get(symbol, symbol.replace("/", ""))


_SEM = asyncio.Semaphore(3)      # protect TwelveData credits when the broker feed is absent


async def analyze(symbol: str, with_cbdr: bool = True) -> Dict:
    from services.ohlc_service import fetch_ohlc, fetch_ohlc_raw
    async with _SEM:
        w1 = await fetch_ohlc(symbol, "1week", 60)
        d1 = await fetch_ohlc(symbol, "1day", 150)
        h4 = await fetch_ohlc_raw(symbol, "4h", 150)
    if len(d1) < 25:
        return {"symbol": display(symbol), "status": "NO_DATA",
                "reason": "need ≥25 daily bars (EA feed or TwelveData)"}
    states = {"W1": bb.state([b[4] for b in w1]) if w1 else None,
              "D1": bb.state([b[4] for b in d1]),
              "H4": bb.state([b["close"] for b in h4]) if h4 else None}
    price = h4[-1]["close"] if h4 else d1[-1][4]
    setup = bb.swing_setup(states, [b[2] for b in d1], [b[3] for b in d1],
                           [b[4] for b in d1], price)
    if with_cbdr and setup.get("status") != "NONE":
        try:
            from cbdr.engine import build_cbdr
            from services.cbdr_service import fetch_cbdr_window
            box = await fetch_cbdr_window(symbol, "cbdr")
            if box:
                c = build_cbdr(box["high"], box["low"])
                tol = 0.25 * setup["levels"]["atr_d1"]
                setup = bb.cbdr_confluence(setup, c.levels, states, tol)
                setup["cbdr_box"] = {"session": box["session"], "high": box["high"],
                                     "low": box["low"]}
        except Exception:
            pass
    setup = apply_bias(setup, macro_bias().get(display(symbol)))
    try:                                    # cached AI sentiment as context (no API call here)
        from services.market_sentiment import read
        r = read(display(symbol))
        if r["composite"] is not None:
            setup["sentiment"] = r
        from derivs.expiry import flags_for
        import datetime as _dt
        ex = flags_for(display(symbol), _dt.date.today())
        if ex:
            setup["expiry"] = ex
            setup.setdefault("blockers", [])
            setup["notes"] = [f"[EXPIRY] {e['market']} {e['type']} {e['date']} — pinning/vol risk"
                              for e in ex]
    except Exception:
        pass
    return {"symbol": display(symbol), "td_symbol": symbol, **setup,
            "bands": {tf: ({k: s[k] for k in ("z", "zone", "mid", "up1", "lo1", "up2", "lo2",
                                                "walk_up", "walk_down", "squeeze")} if s else None)
                      for tf, s in states.items()}}


_RANK = {"ENTRY": 0, "LIMIT": 1, "WATCH": 2, "TREND": 3, "NONE": 4, "NO_DATA": 5}


async def scan(symbols: Optional[List[str]] = None) -> Dict:
    syms = symbols or universe()
    results = await asyncio.gather(*(analyze(s) for s in syms), return_exceptions=True)
    rows = [r if isinstance(r, dict) else {"symbol": display(s), "status": "ERROR",
                                           "reason": str(r)[:120]}
            for s, r in zip(syms, results)]
    rows.sort(key=lambda r: (_RANK.get(r.get("status"), 9), -(r.get("score") or 0)))
    return {"count": len(rows),
            "entries": [r["symbol"] for r in rows if r.get("status") in ("ENTRY", "LIMIT")],
            "results": rows}


def format_alert(r: Dict) -> str:
    lv = r.get("levels", {})
    arrow = "🔻 SELL" if r.get("side") == "SELL" else "🔺 BUY"
    lines = [f"🎯 *Swing fade — {r['symbol']}* {arrow}",
             f"Status: *{r.get('status')}*  ·  score {r.get('score')}/10",
             f"Zones: " + ", ".join(f"{k} {v}" for k, v in (r.get('zones') or {}).items()),
             f"Entry {lv.get('entry')}  ·  SL {lv.get('stop')}",
             f"TP1 (±1σ) {lv.get('tp1_dev1')}  ·  TP2 (mean) {lv.get('tp2_mean')}  ·  R:R {lv.get('rr_to_mean')}"]
    try:                                      # owner's target-based lot ladder
        from propfirm.target_lots import card_lines
        lad = card_lines(r["symbol"], float(lv["entry"]), float(lv["stop"]),
                         [{"name": "TP1", "price": lv["tp1_dev1"]},
                          {"name": "TP2", "price": lv["tp2_mean"]}])
        if lad:
            lines += lad
    except Exception:
        pass
    if r.get("triggers"):
        lines.append("Trigger: close back inside 2σ on " + "/".join(r["triggers"]))
    if r.get("sentiment"):
        se = r["sentiment"]
        parts = ", ".join(f"{k} {v:+.2f}" for k, v in se["components"].items() if v is not None)
        lines.append(f"Sentiment: {se['bias']} {se['composite']:+.2f} ({parts})")
    for n in r.get("notes", []):
        lines.append(n)
    if r.get("cbdr_confluence"):
        m = r["cbdr_confluence"][0]
        lines.append(f"CBDR {m['cbdr']} ≈ {m['band']} → limit {r.get('limit_entry')}")
    lines.append("_Size via /propfirm/challenge (0.5% FundingPips)_")
    return "\n".join(lines)
