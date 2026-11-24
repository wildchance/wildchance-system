"""Session trader — the Asian (pre-London) box turned into TRADES, steered by Monday.

1. MONDAY LINK  Monday's CBDR box (14:00–20:00 NY) is the week's anchor; the weekly map
   gives the bias (COT / weekly profile). Every later daily CBDR box is placed on
   Monday's ladder (0 = Monday high, −1 = Monday low): stepping further in the bias
   direction = CONTINUING, back across Monday's mean = REVERSING, else PAUSING.
   Trades are only taken with the bias and never while REVERSING.
2. ASIAN BOX    19:00 → 02:45 NY (15m bars); levels ±1/±2/±3 box ranges.
3. TRIGGERS     on 15m closes after 03:00 NY (London):
     SWEEP    price runs the box edge AGAINST the bias (Judas swing) then closes back
              inside the box → enter with the bias; stop beyond the sweep extreme
     BREAKOUT a 15m close beyond ±1SD WITH the bias → continuation; stop at the box mid
4. TARGETS      box mid / far edge / ±1SD…, plus the NEAREST OPTIONS EXPIRY levels
                (pin = ATM strike, 25Δ strikes from data/options_strikes.csv) and the
                CME / dealer walls — expiries pull price into their strikes.
Every setup passes services.trade_gate (blackouts, R:R, min stop). One card per
symbol per day.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re
from typing import Dict, List, Optional, Sequence

from decouple import config

FRESH_MIN = int(config("SESSION_FRESH_MIN", default="35"))    # a trigger older than this is stale
SYMBOLS = [s.strip() for s in str(config("SESSION_SYMBOLS", default=(
    "EUR/USD,GBP/USD,USD/JPY,USD/CHF,USD/CAD,XAU/USD"))).split(",") if s.strip()]
STRIKES_CSV = str(config("OPTIONS_STRIKES_CSV", default="data/options_strikes.csv"))


def _t(b: Dict) -> dt.datetime:
    return dt.datetime.fromisoformat(str(b["time"])[:19])


# ── 1. Monday link ──────────────────────────────────────────────────────────────
def trend_state(monday: Optional[tuple], days: Dict[dt.date, tuple], side: Optional[str]) -> Dict:
    """Place each post-Monday daily CBDR box on Monday's ladder."""
    if not monday or not side or monday[1] <= monday[0]:
        return {"state": "UNKNOWN", "path": []}
    lo, hi = monday
    rng = hi - lo
    path = [{"date": d.isoformat(), "k": round(((b[0] + b[1]) / 2 - hi) / rng, 2)} for d, b in sorted(days.items())]
    if not path:
        return {"state": "MONDAY ONLY", "path": []}
    k_last = path[-1]["k"]
    k_prev = path[-2]["k"] if len(path) > 1 else -0.5            # Monday's own mid
    sgn = 1 if side == "BUY" else -1
    if (k_last + 0.5) * sgn < -0.25:                              # back across Monday's mean
        state = "REVERSING"
    elif (k_last - k_prev) * sgn > 0.1 or (k_last * sgn > 0 if side == "BUY" else k_last < -1):
        state = "CONTINUING"
    else:
        state = "PAUSING"
    return {"state": state, "path": path}


# ── 2. Asian box ────────────────────────────────────────────────────────────────
def asian_box(bars: Sequence[Dict], today: dt.date) -> Optional[Dict]:
    """19:00 (yesterday) → 02:45 NY, from NY-local 15m bars."""
    start = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(19, 0))
    end = dt.datetime.combine(today, dt.time(2, 45))
    sel = [b for b in bars if start <= _t(b) < end]
    if len(sel) < 8:
        return None
    hi, lo = max(b["high"] for b in sel), min(b["low"] for b in sel)
    if hi <= lo:
        return None
    r = hi - lo
    return {"high": hi, "low": lo, "mid": (hi + lo) / 2, "range": r, "bars": len(sel),
            "levels": {f"{k:+d}SD": (hi + k * r if k > 0 else lo + k * r) for k in (-3, -2, -1, 1, 2, 3)}}


# ── 3. triggers ─────────────────────────────────────────────────────────────────
def find_trigger(after: Sequence[Dict], box: Dict, side: str) -> Optional[Dict]:
    """First SWEEP-reclaim or BREAKOUT on 15m closes after the box (oldest-first)."""
    hi, lo, lv = box["high"], box["low"], box["levels"]
    swept, extreme = False, None
    for b in after:
        if side == "SELL":
            if b["high"] > hi:
                swept, extreme = True, max(extreme or b["high"], b["high"])
            if swept and b["close"] < hi:
                return {"kind": "SWEEP", "time": str(b["time"]), "entry": b["close"], "extreme": extreme}
            if b["close"] < lv["-1SD"]:
                return {"kind": "BREAKOUT", "time": str(b["time"]), "entry": b["close"], "extreme": box["mid"]}
        else:
            if b["low"] < lo:
                swept, extreme = True, min(extreme or b["low"], b["low"])
            if swept and b["close"] > lo:
                return {"kind": "SWEEP", "time": str(b["time"]), "entry": b["close"], "extreme": extreme}
            if b["close"] > lv["+1SD"]:
                return {"kind": "BREAKOUT", "time": str(b["time"]), "entry": b["close"], "extreme": box["mid"]}
    return None


# ── 4. targets: box + nearest options expiry ───────────────────────────────────
def expiry_levels(symbol: str, today: dt.date, path: str = STRIKES_CSV) -> List[Dict]:
    """Nearest expiry ≥ today: pin (ATM strike) + 25Δ strikes, from the owner's vol screens."""
    if not os.path.exists(path):
        return []
    s = symbol.upper().replace("/", "")
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                if r["symbol"].upper().replace("/", "") == s and dt.date.fromisoformat(r["expiry"]) >= today:
                    rows.append(r)
            except (KeyError, ValueError):
                continue
    if not rows:
        return []
    r = min(rows, key=lambda x: x["expiry"])
    exp = dt.date.fromisoformat(r["expiry"])
    out = []
    m = re.search(r"ATM ([\d.]+)", r.get("source") or "")
    if m:
        out.append({"price": float(m.group(1)), "label": f"pin {exp:%d-%b}"})
    for col, name in (("put25", "25Δ put"), ("call25", "25Δ call")):
        if (r.get(col) or "").strip():
            out.append({"price": float(r[col]), "label": f"{name} {exp:%d-%b}"})
    return out


def targets(side: str, entry: float, box: Dict, extra: Sequence[Dict]) -> List[Dict]:
    """TP1/TP2 from the Asian box, TP3 = the nearest options-expiry / dealer level beyond."""
    lv = box["levels"]
    sgn = -1 if side == "SELL" else 1
    box_t = [{"price": box["mid"], "label": "Asia mid"},
             {"price": box["low"] if side == "SELL" else box["high"], "label": "Asia far edge"},
             {"price": lv["-1SD"] if side == "SELL" else lv["+1SD"], "label": "Asia ∓1SD"},
             {"price": lv["-2SD"] if side == "SELL" else lv["+2SD"], "label": "Asia ∓2SD"}]
    ahead = sorted((c for c in box_t if (c["price"] - entry) * sgn > 0.15 * box["range"]),
                   key=lambda c: abs(c["price"] - entry))
    out = [dict(c) for c in ahead[:2]]
    last = out[-1]["price"] if out else entry
    opt = sorted((c for c in extra if (c["price"] - last) * sgn > 0.15 * box["range"]),
                 key=lambda c: abs(c["price"] - last))
    if opt:
        out.append(dict(opt[0]))
    elif len(ahead) > 2:
        out.append(dict(ahead[2]))
    return out


def setup(symbol: str, side: str, box: Dict, trig: Dict, extra: Sequence[Dict]) -> Optional[Dict]:
    from services import trade_gate as tg
    entry = trig["entry"]
    buf = 0.1 * box["range"]
    stop = trig["extreme"] + buf if side == "SELL" else trig["extreme"] - buf
    stop = tg.widen_stop(symbol, side, entry, stop)
    tps = targets(side, entry, box, extra)
    if not tps or (stop - entry) * (1 if side == "SELL" else -1) <= 0:
        return None
    risk = abs(entry - stop)
    for i, t in enumerate(tps, 1):
        t["name"], t["r"] = f"TP{i}", round(abs(t["price"] - entry) / risk, 2)
    return {"symbol": symbol, "side": side, "kind": trig["kind"], "time": trig["time"],
            "entry": entry, "stop": round(stop, 5), "targets": tps}


def stale_reason(s: Dict, after: Sequence[Dict], now: dt.datetime, fresh_min: int = FRESH_MIN) -> Optional[str]:
    """Only act on a trigger that just happened and is still tradeable at the last price."""
    t = dt.datetime.fromisoformat(str(s["time"])[:19])
    if (now - t).total_seconds() > fresh_min * 60 + 15 * 60:       # trigger bar closes 15 min after its stamp
        return f"stale trigger ({t:%H:%M} NY) — not chased"
    last = after[-1]["close"] if after else s["entry"]
    sgn = -1 if s["side"] == "SELL" else 1
    if (last - s["stop"]) * sgn <= 0:
        return "price already through the stop"
    if (last - s["targets"][0]["price"]) * sgn >= 0:
        return "TP1 already reached — missed"
    if abs(last - s["entry"]) > 0.5 * abs(s["entry"] - s["stop"]):
        return "price ran > 0.5R from the entry — not chased"
    return None


def card(s: Dict, trend: Dict, bias_why: str) -> str:
    from routes.cbdr import px
    sym = s["symbol"]
    icon = "🔴" if s["side"] == "SELL" else "🟢"
    kind = ("Asia box SWEPT then reclaimed (Judas swing)" if s["kind"] == "SWEEP"
            else "Asia box BROKEN with the week's bias (continuation)")
    lines = [f"🌏 *Asia → London — {sym}* {icon} {s['side']}", kind,
             f"Monday link: *{trend['state']}* · {bias_why}"]
    if trend.get("path"):
        lines.append("Daily boxes on Monday's ladder: " + " → ".join(f"{p['date'][5:]} {p['k']:+.2f}" for p in trend["path"]))
    lines += ["", f"Entry: `{px(sym, s['entry'])}`", f"Stop:  `{px(sym, s['stop'])}`"]
    for t in s["targets"]:
        lines.append(f"{t['name']}:   `{px(sym, t['price'])}`  (R {t['r']}) {t['label']}")
    if s.get("gate_reasons"):
        lines.append("⚠️ " + "; ".join(s["gate_reasons"]))
    return "\n".join(lines)


async def run_one(symbol: str, notify: bool = False, now: Optional[dt.datetime] = None) -> Dict:
    from zoneinfo import ZoneInfo
    from services import trade_gate as tg
    from services import weekly_map as wm
    from services.auto_boxes import cbdr_by_day
    from services.ohlc_service import fetch_ohlc_raw
    now = now or dt.datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    today = now.date()
    if now.hour < 3 or now.hour >= 12:
        return {"symbol": symbol, "ok": False, "reason": "outside London (03:00–12:00 NY)"}
    m15 = await fetch_ohlc_raw(symbol, "15min", 200, timezone="America/New_York")
    h1 = await fetch_ohlc_raw(symbol, "1h", 140, timezone="America/New_York")
    box = asian_box(m15 or [], today)
    if not box:
        return {"symbol": symbol, "ok": False, "reason": "Asian box not formed / no 15m data"}
    by_day = cbdr_by_day(h1 or [])
    mon = wm.monday_box(by_day, today)
    res = await wm.run(symbol) if mon else {}
    bias = (res or {}).get("bias") or {}
    side = bias.get("side")
    monday = (mon["low"], mon["high"]) if mon else None
    later = {d: b for d, b in by_day.items() if mon and d > dt.date.fromisoformat(mon["date"]) and d < today}
    trend = trend_state(monday, later, side)
    out = {"symbol": symbol, "ok": True, "box": box, "bias": bias, "trend": trend}
    if not side:
        return {**out, "setup": None, "reason": "no weekly bias — map only"}
    if trend["state"] == "REVERSING":
        return {**out, "setup": None, "reason": "daily boxes back across Monday's mean — bias paused"}
    after = [b for b in (m15 or []) if _t(b) >= dt.datetime.combine(today, dt.time(3, 0))]
    trig = find_trigger(after, box, side)
    if not trig:
        return {**out, "setup": None, "reason": "no sweep-reclaim or breakout yet"}
    extra = expiry_levels(symbol, today)
    try:
        from services import retail_levels
        extra += retail_levels.levels(symbol)
    except Exception:
        pass
    try:
        from services.options_levels import cached_levels
        extra += [{"price": x["price"], "label": x["label"]} for x in cached_levels(symbol, after[-1]["close"])
                  if any(w in x["label"] for w in ("wall", "floor", "shelf", "pain", "Closest"))]
    except Exception:
        pass
    s = setup(symbol, side, box, trig, extra)
    if not s:
        return {**out, "setup": None, "reason": "no target ahead"}
    stale = stale_reason(s, after, now)
    if stale:
        return {**out, "setup": s, "reason": stale}
    gate = tg.evaluate(symbol, side, s["entry"], s["stop"], [t["price"] for t in s["targets"]])
    s["gate_reasons"] = gate.get("reasons")
    out["setup"] = s
    if notify and gate.get("allow"):
        from services import council_gate
        cg = await council_gate.check(symbol, side)
        out["council"] = cg["verdict"]
        if cg["pass"]:
            from services.realtime import alert_once
            out["sent"] = await alert_once(f"asia:{symbol}:{today}",
                                           card(s, trend, bias.get("why", "")) + "\n" + cg["line"], ttl_hours=20)
            if out["sent"]:
                from services import signal_tracker
                await signal_tracker.record(symbol, side, s["entry"], s["stop"],
                                            [t["price"] for t in s["targets"]], "asia-london")
    return out


async def run(notify: bool = False) -> Dict:
    res = []
    for s in SYMBOLS:
        try:
            res.append(await run_one(s, notify))
        except Exception as e:
            res.append({"symbol": s, "ok": False, "reason": f"{type(e).__name__}: {str(e)[:80]}"})
    return {"results": [{k: v for k, v in r.items() if k != "box"} for r in res]}
