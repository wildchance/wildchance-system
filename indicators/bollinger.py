"""Bollinger ±1σ / ±2σ multi-timeframe mean-reversion engine (pure, no I/O).

The idea (classic "double Bollinger" read):
  • inside ±1σ ............ CORE      — fair value, no edge for a fade
  • between 1σ and 2σ ...... STRETCHED — trend zone; fade only with a trigger
  • beyond ±2σ ............. EXTREME   — statistically rare; mean-reversion candidate
  • close back INSIDE 2σ after a close outside = RE-ENTRY trigger (the fade entry)
  • 4 of the last 5 closes beyond 1σ = BAND WALK → strong trend, do NOT fade
  • bandwidth in its lowest decile = SQUEEZE → breakout risk, do NOT fade

Higher timeframes carry more weight (W1 3 · D1 2 · H4 1). A swing fade needs the
HTF stretched/extreme AND a re-entry trigger, without a band walk against it.
Targets sit back inside the bands: TP1 = the D1 ±1σ line, TP2 = the D1 20-period
mean. Stop = beyond the recent D1 extreme + 0.25×ATR.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

PERIOD = 20
TF_WEIGHT = {"W1": 3.0, "D1": 2.0, "H4": 1.0}


def bands(closes: Sequence[float], period: int = PERIOD) -> Optional[Dict[str, float]]:
    """Bands for the LAST close: mid (SMA), sd (population), ±1σ, ±2σ, z, bandwidth."""
    if len(closes) < period:
        return None
    win = [float(x) for x in closes[-period:]]
    mid = sum(win) / period
    sd = math.sqrt(sum((x - mid) ** 2 for x in win) / period)
    c = float(closes[-1])
    z = (c - mid) / sd if sd > 0 else 0.0
    return {"close": c, "mid": mid, "sd": sd, "up1": mid + sd, "lo1": mid - sd,
            "up2": mid + 2 * sd, "lo2": mid - 2 * sd, "z": z,
            "bandwidth": (4 * sd / mid) if mid else 0.0}


def zone(z: float) -> str:
    if z >= 2:
        return "EXTREME_HIGH"
    if z >= 1:
        return "STRETCHED_HIGH"
    if z <= -2:
        return "EXTREME_LOW"
    if z <= -1:
        return "STRETCHED_LOW"
    return "CORE"


def state(closes: Sequence[float], period: int = PERIOD, walk_n: int = 5) -> Optional[Dict]:
    """Full Bollinger read of the latest closed bar of one timeframe."""
    if len(closes) < period + walk_n:
        return None
    b = bands(closes, period)
    prev = bands(closes[:-1], period)
    zs = []
    for i in range(walk_n):
        bi = bands(closes[: len(closes) - i], period)
        zs.append(bi["z"] if bi else 0.0)
    walk_up = sum(z >= 1 for z in zs) >= walk_n - 1
    walk_down = sum(z <= -1 for z in zs) >= walk_n - 1
    widths = [bands(closes[: len(closes) - i], period)["bandwidth"]
              for i in range(0, min(120, len(closes) - period + 1))]
    squeeze = False
    if len(widths) >= 30:
        cutoff = sorted(widths)[max(0, len(widths) // 10 - 1)]
        squeeze = min(widths[:3]) <= cutoff
    reentry_down = prev["close"] >= prev["up2"] and b["close"] < b["up2"]
    reentry_up = prev["close"] <= prev["lo2"] and b["close"] > b["lo2"]
    return {**{k: round(v, 6) for k, v in b.items()}, "zone": zone(b["z"]),
            "prev_z": round(prev["z"], 3), "walk_up": walk_up, "walk_down": walk_down,
            "squeeze": squeeze, "reentry_down": reentry_down, "reentry_up": reentry_up}


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> Optional[float]:
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(n - period, n)]
    return sum(trs) / period


def _zone_points(z: Optional[str], side: str, ext: float, stretch: float) -> float:
    want = "HIGH" if side == "SELL" else "LOW"
    if not z or not z.endswith(want):
        return 0.0
    return ext if z.startswith("EXTREME") else stretch


def swing_setup(states: Dict[str, Optional[Dict]], d1_highs: Sequence[float],
                d1_lows: Sequence[float], d1_closes: Sequence[float],
                price: Optional[float] = None) -> Dict:
    """Combine W1/D1/H4 states into one mean-reversion read for a symbol.

    Returns side (SELL fades a high extreme, BUY fades a low), score 0–10,
    status ENTRY / WATCH / TREND / NONE, blockers, and entry/stop/targets."""
    present = {tf: s for tf, s in states.items() if s}
    if "D1" not in present:
        return {"status": "NONE", "reason": "no D1 bands (need ≥ 25 daily closes)"}
    wsum = sum(TF_WEIGHT[tf] for tf in present)
    wz = sum(TF_WEIGHT[tf] * max(-3.0, min(3.0, s["z"])) for tf, s in present.items()) / wsum
    side = "SELL" if wz > 0 else "BUY"
    d1, h4, w1 = present.get("D1"), present.get("H4"), present.get("W1")

    score = 0.0
    score += _zone_points(w1 and w1["zone"], side, 3, 2)
    score += _zone_points(d1["zone"], side, 3, 2)
    score += _zone_points(h4 and h4["zone"], side, 1, 0.5)
    trig_key = "reentry_down" if side == "SELL" else "reentry_up"
    triggers = [tf for tf in ("H4", "D1") if present.get(tf) and present[tf][trig_key]]
    if triggers:
        score += 2
    walk_key = "walk_up" if side == "SELL" else "walk_down"
    blockers = []
    if d1[walk_key] and not triggers:
        blockers.append("D1 band walk — trend, don't fade")
    if w1 and w1[walk_key]:
        blockers.append("W1 band walk")
    if d1["squeeze"]:
        blockers.append("D1 squeeze — breakout risk")
    if not blockers:
        score += 1
    score = round(min(score, 10.0), 1)

    htf_ok = _zone_points(d1["zone"], side, 1, 1) > 0 or _zone_points(w1 and w1["zone"], side, 1, 1) > 0
    if any("walk" in b for b in blockers) and not triggers:
        status = "TREND"
    elif htf_ok and triggers and score >= 6 and not blockers:
        status = "ENTRY"
    elif htf_ok and score >= 4:
        status = "WATCH"
    else:
        status = "NONE"

    px = float(price if price is not None else d1["close"])
    a = atr(d1_highs, d1_lows, d1_closes) or d1["sd"] * 0.5
    sgn = -1 if side == "SELL" else 1
    if side == "SELL":
        stop = max(d1_highs[-3:]) + 0.25 * a
        tp1 = d1["up1"] if d1["up1"] < px else d1["mid"]
    else:
        stop = min(d1_lows[-3:]) - 0.25 * a
        tp1 = d1["lo1"] if d1["lo1"] > px else d1["mid"]
    tp2 = d1["mid"]
    risk = abs(px - stop)
    reward = (tp2 - px) * sgn
    rr = round(reward / risk, 2) if risk > 0 and reward > 0 else 0.0
    return {
        "side": side, "status": status, "score": score, "weighted_z": round(wz, 2),
        "zones": {tf: s["zone"] for tf, s in present.items()},
        "triggers": triggers, "blockers": blockers,
        "levels": {"entry": round(px, 5), "stop": round(stop, 5), "tp1_dev1": round(tp1, 5),
                   "tp2_mean": round(tp2, 5), "rr_to_mean": rr, "atr_d1": round(a, 5),
                   "d1_up2": round(d1["up2"], 5), "d1_lo2": round(d1["lo2"], 5),
                   "d1_up1": round(d1["up1"], 5), "d1_lo1": round(d1["lo1"], 5)},
    }


def cbdr_confluence(setup: Dict, cbdr_levels: Dict[str, float], states: Dict[str, Optional[Dict]],
                    tol: float) -> Dict:
    """Do CBDR ±SD projections sit on the HTF Bollinger extremes (same side)?

    A CBDR +2SD landing on the D1 +2σ is two independent measures of the same
    extreme → the intraday limit level for a swing fade. Adds +2 to the score."""
    side = setup.get("side")
    want = "+" if side == "SELL" else "-"
    band_key = ("up2", "up1") if side == "SELL" else ("lo2", "lo1")
    matches = []
    for tf in ("W1", "D1", "H4"):
        s = states.get(tf)
        if not s:
            continue
        for bk in band_key:
            for name, lvl in cbdr_levels.items():
                if name.startswith(want) and abs(lvl - s[bk]) <= tol:
                    matches.append({"cbdr": name, "cbdr_price": round(lvl, 5),
                                    "band": f"{tf} {bk}", "band_price": round(s[bk], 5)})
    out = dict(setup)
    if matches:
        out["score"] = round(min(10.0, setup.get("score", 0) + 2), 1)
        out["cbdr_confluence"] = matches
        best = min(matches, key=lambda m: abs(m["cbdr_price"] - m["band_price"]))
        out["limit_entry"] = round((best["cbdr_price"] + best["band_price"]) / 2, 5)
        if out.get("status") == "WATCH" and out["score"] >= 6 and not out.get("blockers"):
            out["status"] = "LIMIT"           # rest a limit at the shared level
    return out


def as_watchlist_item(symbol: str, setup: Dict) -> Dict:
    """Feed a swing fade into the 4-step pipeline (it lands in the RANGE tier)."""
    lv = setup.get("levels", {})
    return {"symbol": symbol, "side": setup.get("side"), "rr": lv.get("rr_to_mean"),
            "range_bound": True, "entry": lv.get("entry"), "stop": lv.get("stop"),
            "sigma": abs((lv.get("d1_up1") or 0) - (lv.get("tp2_mean") or 0)) or None,
            "bb_score": setup.get("score"), "bb_status": setup.get("status")}
