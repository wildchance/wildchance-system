"""Walk-forward backtests (research layer B) — nothing scales up without evidence.

Replays an engine's PRICE rules bar by bar on history and resolves every trade with the
same logic the live tracker uses (signal_tracker.step: stop before TP1 = −1R, TP1 then
breakeven, equal split across targets). Engines:

  z      Z-setup: 1h 20-bar z-score, FRESH breach |z| ≥ 2 → fade to the mean; stop one σ
         beyond the extreme, floored at ZC_MIN_RISK_ATR × daily ATR(14); no entries while the
         CBDR box forms (14:00–20:00 NY). Up to ~5000 hourly bars.
  asia   Asia→London: 19:00→02:45 NY box, first 15m sweep-reclaim / breakout after 03:00,
         either side (the weekly COT bias is not replayable yet — the point-in-time store
         fixes that going forward). Free 15m history ≈ 2 months.

Reported per engine × symbol: trades, win rate, expectancy (R), profit factor, max
drawdown (R), Sharpe (per trade × √trades/yr), and WALK-FORWARD: trades split into 4
sequential folds — the edge is 'stable' when ≥ 3 folds have positive expectancy.
Results are stored (data_store 'backtest:{engine}:{symbol}') for the council and reports.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _ts(b: Dict) -> float:
    return dt.datetime.fromisoformat(str(b["time"])[:19]).replace(tzinfo=NY).timestamp()


def resolve(sig: Dict, bars: List[Dict], start_i: int, max_bars: int) -> Dict:
    """Run one signal through the following bars with the live tracker's rules."""
    from services import signal_tracker as st
    from services.snapshots import realised_r
    window = [{**b, "ts": _ts(b)} for b in bars[start_i + 1:start_i + 1 + max_bars]]
    st.step(sig, window, now=sig["sent_at"])                 # 'now' = send time → no expiry inside step
    if sig["result"] == "open":                              # time-out: close at the last bar
        last = window[-1]["close"] if window else sig["entry"]
        sgn = 1 if sig["side"] == "BUY" else -1
        risk = abs(sig["entry"] - sig["stop"]) or 1
        r_open = (last - sig["entry"]) * sgn / risk
        sig["result"] = "win" if sig["hits"] else ("breakeven" if abs(r_open) < 0.1 else "win" if r_open > 0 else "loss")
        r = realised_r(sig) if sig["hits"] else round(r_open, 3)
    else:
        r = realised_r(sig)
    return {"t": sig["sent_at"], "side": sig["side"], "result": sig["result"], "r": r if r is not None else 0.0}


def stats(trades: List[Dict], years: float = 1.0) -> Dict:
    n = len(trades)
    if not n:
        return {"trades": 0}
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    losses = sum(1 for r in rs if r < 0)
    gp, gl = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    mean = sum(rs) / n
    sd = (sum((r - mean) ** 2 for r in rs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    per_year = n / max(years, 1 / 52)
    return {"trades": n, "win_rate_pct": round(100 * wins / max(1, wins + losses), 1),
            "expectancy_r": round(mean, 3), "total_r": round(sum(rs), 2),
            "profit_factor": round(gp / gl, 2) if gl else None, "max_drawdown_r": round(mdd, 2),
            "sharpe": round(mean / sd * math.sqrt(per_year), 2) if sd else None}


def walk_forward(trades: List[Dict], folds: int = 4) -> Dict:
    if len(trades) < folds * 3:
        return {"folds": [], "stable": None, "note": f"need ≥ {folds * 3} trades"}
    k = len(trades) // folds
    parts = [trades[i * k:(i + 1) * k if i < folds - 1 else len(trades)] for i in range(folds)]
    fs = [stats(p) for p in parts]
    pos = sum(1 for f in fs if f["expectancy_r"] > 0)
    return {"folds": [{"trades": f["trades"], "expectancy_r": f["expectancy_r"], "win_rate_pct": f["win_rate_pct"]}
                      for f in fs], "positive_folds": pos, "stable": pos >= folds - 1}


def daily_atr(daily: List[tuple], n: int = 14) -> Dict[dt.date, float]:
    out, trs = {}, []
    for i, (d, _o, h, l, c) in enumerate(daily):
        if i:
            trs.append(max(h - l, abs(h - daily[i - 1][4]), abs(l - daily[i - 1][4])))
        if len(trs) >= n:
            out[d] = sum(trs[-n:]) / n
    return out


def z_trades(symbol: str, bars: List[Dict], atr_by_day: Dict[dt.date, float], window: int = 20,
             z_thr: float = 2.0, max_bars: int = 72) -> List[Dict]:
    from intraday.engine import z_at
    from services import signal_tracker as st
    from services import zconfluence as zc
    closes = [b["close"] for b in bars]
    out, prev_fired = [], False
    for i in range(window, len(bars) - 1):
        z = z_at(closes, i, window)
        fired = z is not None and abs(z) >= z_thr
        fresh, prev_fired = fired and not prev_fired, fired
        if not fresh:
            continue
        t = dt.datetime.fromisoformat(str(bars[i]["time"])[:19])
        if 14 <= t.hour < 20 or t.weekday() >= 5:
            continue
        win = closes[i - window + 1:i + 1]
        m = sum(win) / window
        sd = (sum((x - m) ** 2 for x in win) / (window - 1)) ** 0.5
        lean = "SELL" if z > 0 else "BUY"
        prior = [d for d in atr_by_day if d < t.date()]
        floor = zc.MIN_RISK_ATR * atr_by_day[max(prior)] if prior else 0.0
        lv = zc.levels(lean, closes[i], m, sd, z, symbol, min_risk=floor)
        if not lv or not lv.get("tp2"):
            continue
        sig = st.new_signal(symbol, lean, lv["entry"], lv["stop"], [lv.get("tp1"), lv.get("tp2")], "bt:z",
                            ts=_ts(bars[i]))
        if sig["targets"]:
            out.append(resolve(sig, bars, i, max_bars))
    return out


def asia_trades(symbol: str, bars15: List[Dict]) -> List[Dict]:
    from services import session_trader as stt
    from services import signal_tracker as st
    days = sorted({dt.datetime.fromisoformat(str(b["time"])[:19]).date() for b in bars15})
    out = []
    for d in days:
        if d.weekday() >= 5:
            continue
        box = stt.asian_box(bars15, d)
        if not box:
            continue
        t0, t1 = dt.datetime.combine(d, dt.time(3, 0)), dt.datetime.combine(d, dt.time(12, 0))
        idx = [i for i, b in enumerate(bars15) if t0 <= dt.datetime.fromisoformat(str(b["time"])[:19]) < t1]
        if not idx:
            continue
        after = [bars15[i] for i in idx]
        trigs = [(tr, side) for side in ("BUY", "SELL") if (tr := stt.find_trigger(after, box, side))]
        if not trigs:
            continue
        tr, side = min(trigs, key=lambda x: x[0]["time"])
        s = stt.setup(symbol, side, box, tr, [])
        if not s:
            continue
        i = next(j for j in idx if str(bars15[j]["time"]) == tr["time"])
        sig = st.new_signal(symbol, side, s["entry"], s["stop"], [t["price"] for t in s["targets"]], "bt:asia",
                            ts=_ts(bars15[i]))
        if sig["targets"]:
            out.append(resolve(sig, bars15, i, 4 * 24))
    return out


async def run(engine: str, symbol: str) -> Dict:
    from services import data_store
    from services.ohlc_service import fetch_ohlc, fetch_ohlc_raw
    if engine == "z":
        bars = await fetch_ohlc_raw(symbol, "1h", 5000, timezone="America/New_York")
        daily = await fetch_ohlc(symbol, "1day", 400)
        trades = z_trades(symbol, bars or [], daily_atr(daily or []))
    elif engine == "asia":
        bars = await fetch_ohlc_raw(symbol, "15min", 5000, timezone="America/New_York")
        trades = asia_trades(symbol, bars or [])
    else:
        return {"ok": False, "reason": "engine must be z or asia"}
    if not bars:
        return {"ok": False, "reason": "no history"}
    span = (_ts(bars[-1]) - _ts(bars[0])) / (365 * 86400)
    res = {"ok": True, "engine": engine, "symbol": symbol, "from": str(bars[0]["time"])[:10],
           "to": str(bars[-1]["time"])[:10], "years": round(span, 2), **stats(trades, span),
           "walk_forward": walk_forward(trades), "by_side": {s: stats([t for t in trades if t["side"] == s], span)
                                                             for s in ("BUY", "SELL")}}
    await data_store.put(f"backtest:{engine}:{symbol.upper().replace('/', '')}", res)
    return res


async def edge(engine: str, symbol: str) -> Optional[Dict]:
    from services import data_store
    v, _ = await data_store.get(f"backtest:{engine}:{symbol.upper().replace('/', '')}")
    return v
