"""Optimus Prime — zone-PRECISION locator (pure).

The engine that fixes the miss: price reached 4133 (bullish mean), reversed at the
4152/4163 up/down-close supply, and an EARLY entry there would have hit the 250-usd
stop. Optimus locates these zones exactly and only fires on the reject, then maps the
move onto the 250-usd / 2500-pip capture grid and anticipates the next zone.

  1. LOCATE  — the exact last up-close (bearish OB) / down-close (bullish OB) candle
     at a reaction level → the precise wick+close zone (e.g. 4152–4163).
  2. GATE    — ARMED only when price sweeps the zone and CLOSES BACK inside (reject).
     No reject = WAIT. This is the discipline that stops the early-entry 250-usd SL.
  3. CAPTURE — entry at the zone, stop just beyond the wick (small), target the next
     opposing zone; graded on the Big-5 250-pip floor → the full 2500-pip / 250-usd bag.
  4. ANTICIPATE — the next zone in the trade direction (sell 4163 → 3987 4H OB → 3885).

The live reaction map is operator-fed (set_live_zones) and defaults to the
2026-07-24 4H chart. Reuses gold.rejection (reject trigger) + gold.big5 (capture grade).
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from gold.big5 import tier_for_pips, MIN_CAPTURE_PIPS
from gold.risk_engine import GOLD_PIP

_STOP_BUFFER = 1.5      # stop beyond the OB wick, in price

# Live reaction map — 2026-07-24 4H chart. Operator-updatable via set_live_zones().
LIVE_ZONES = {
    "sell": [
        {"name": "supply_4179_4190", "lo": 4179.79, "hi": 4190.60, "note": "higher supply"},
        {"name": "supply_4152_4163", "lo": 4152.40, "hi": 4163.07,
         "note": "up/down-close supply — sell-off origin"},
    ],
    "buy": [
        {"name": "ob_4001", "lo": 3995.0, "hi": 4001.60, "note": "shelf"},
        {"name": "ob_3987_4h", "lo": 3980.0, "hi": 3994.0, "note": "4H bullish order block"},
        {"name": "shelf_3944_3958", "lo": 3944.11, "hi": 3958.50, "note": "fib support shelf"},
        {"name": "central_limit_3885", "lo": 3880.0, "hi": 3888.0,
         "note": "central limit — last floor before the void"},
        # --- below here is the W1 no-floor VOID: nothing real until ~3506 ---
        {"name": "weekly_buy_3506", "lo": 3500.0, "hi": 3512.0,
         "note": "weekly buy limit — first floor across the void"},
        {"name": "macro_buy_3291", "lo": 3285.0, "hi": 3298.0,
         "note": "macro accumulation floor"},
    ],
    "pivots": {"bullish_mean": 4133.90},
}

# A gap larger than this (in pips) with no zone = a no-floor VOID: price travels it
# fast, so the efficient target is the far side, not an imaginary level inside it.
VOID_MIN_PIPS = 2000.0        # 2000 pips = 200 usd of air


def set_live_zones(sell: Sequence[dict] = None, buy: Sequence[dict] = None,
                   pivots: dict = None) -> dict:
    """Operator update of the live reaction map (feed today's up/down-close zones)."""
    if sell is not None:
        LIVE_ZONES["sell"] = [dict(z) for z in sell]
    if buy is not None:
        LIVE_ZONES["buy"] = [dict(z) for z in buy]
    if pivots is not None:
        LIVE_ZONES["pivots"] = dict(pivots)
    return {k: LIVE_ZONES[k] for k in LIVE_ZONES}


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
    if len(bar) >= 5:
        return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))
    return (float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]))


def _pips(a: float, b: float) -> float:
    return round(abs(a - b) / GOLD_PIP, 1)


def locate_ob(bars: Sequence, zone: dict, side: str) -> Optional[dict]:
    """The exact OB candle at a reaction zone: for a SELL zone the last UP-CLOSE
    candle whose body sits in the band (supply reload); for a BUY zone the last
    DOWN-CLOSE candle in the band (demand). Returns the refined wick+close zone."""
    lo, hi = zone["lo"], zone["hi"]
    want_up = side == "sell"                 # bearish OB = last up-close before drop
    best = None
    for i, b in enumerate(bars):
        o, h, l, c = _ohlc(b)
        in_band = lo - 3 <= max(o, c) <= hi + 3 and (l <= hi and h >= lo)
        if not in_band:
            continue
        if want_up and c > o:                # up-close in a sell band
            best = (i, o, h, l, c)
        elif (not want_up) and c < o:        # down-close in a buy band
            best = (i, o, h, l, c)
    if best is None:
        return None
    _i, o, h, l, c = best
    if side == "sell":
        ob_zone = [round(min(o, c), 2), round(h, 2)]     # body-top → wick-high
        stop = round(h + _STOP_BUFFER, 2)
    else:
        ob_zone = [round(l, 2), round(max(o, c), 2)]     # wick-low → body-bottom
        stop = round(l - _STOP_BUFFER, 2)
    return {"zone_name": zone["name"], "side": side, "ob_zone": ob_zone,
            "stop": stop, "note": zone.get("note", "")}


def _next_target(side: str, entry: float) -> Optional[dict]:
    """The next opposing zone in the trade direction (the capture target)."""
    if side == "sell":
        buys = sorted((z for z in LIVE_ZONES["buy"] if z["hi"] < entry),
                      key=lambda z: -z["hi"])
        return buys[0] if buys else None
    sells = sorted((z for z in LIVE_ZONES["sell"] if z["lo"] > entry),
                   key=lambda z: z["lo"])
    return sells[0] if sells else None


def target_ladder(side: str, entry: float) -> dict:
    """Map the efficient target cascade in the trade direction, flagging VOIDS — the
    no-floor gaps price travels fast. For a SELL: the buy zones below the entry, in
    order, each tagged with the pip gap from the previous and whether a void precedes
    it. The 'last floor' before a big void is the disciplined take-profit; the far
    side of the void is the extended (whale) target."""
    if side.lower() in ("sell", "short"):
        zones = sorted((z for z in LIVE_ZONES["buy"] if z["hi"] < entry),
                       key=lambda z: -z["hi"])
        edge = lambda z: z["hi"]
    else:
        zones = sorted((z for z in LIVE_ZONES["sell"] if z["lo"] > entry),
                       key=lambda z: z["lo"])
        edge = lambda z: z["lo"]
    ladder, prev, last_floor, void_target = [], entry, None, None
    for z in zones:
        e = edge(z)
        gap = _pips(prev, e)
        void = gap >= VOID_MIN_PIPS
        cum = _pips(entry, e)
        tier = tier_for_pips(cum)
        row = {"zone": z["name"], "target": e, "pips_from_prev": gap,
               "cum_pips": cum, "void_before": void,
               "tier": (tier or {}).get("name"),
               "note": (f"VOID — {gap:.0f} pips of air; efficient runner target"
                        if void else z.get("note", ""))}
        ladder.append(row)
        if void and void_target is None:
            void_target = row
        elif not void:
            last_floor = row
        prev = e
    return {"side": side.upper(), "entry": round(entry, 2), "ladder": ladder,
            "last_floor": last_floor, "void_target": void_target,
            "note": ("efficient targets mapped"
                     + (f"; disciplined TP {last_floor['zone']} @ {last_floor['target']}"
                        if last_floor else "")
                     + (f"; runner across the void to {void_target['zone']} "
                        f"@ {void_target['target']}" if void_target else ""))}


def precision_entry(bars: Sequence, zone: dict, side: str) -> dict:
    """The full precision read for one zone: the located OB, the reject gate (ARMED
    vs WAIT — no early fill), the 250-usd stop, and the capture grade to the next zone."""
    from gold.rejection import sweep_reject
    ob = locate_ob(bars, zone, side)
    if not ob:
        return {"zone": zone["name"], "status": "no OB candle located yet"}
    lo, hi = ob["ob_zone"]
    level = hi if side == "sell" else lo          # the edge liquidity is grabbed at
    rej = sweep_reject(bars, level, side, lookback=3)
    entry = rej["entry"] if rej else round((lo + hi) / 2, 2)
    stop = rej["stop"] if rej else ob["stop"]
    tgt = _next_target(side, entry)
    target_price = (tgt["hi"] if side == "sell" else tgt["lo"]) if tgt else None
    capture_pips = _pips(entry, target_price) if target_price else None
    tier = tier_for_pips(capture_pips) if capture_pips else None
    dist = abs(entry - stop) or 1e-9
    rr = round(abs((target_price or entry) - entry) / dist, 2) if target_price else None
    armed = bool(rej)
    return {
        "zone": zone["name"], "side": side.upper(), "ob_zone": ob["ob_zone"],
        "entry": entry, "stop": stop,
        "risk_pips": _pips(entry, stop),
        "target_zone": tgt["name"] if tgt else None, "target": target_price,
        "capture_pips": capture_pips,
        "capture_tier": (tier or {}).get("name") if tier else None,
        "meets_250_floor": bool(capture_pips and capture_pips >= MIN_CAPTURE_PIPS),
        "rr": rr, "armed": armed,
        "status": ("ARMED — swept + rejected, fire the precision entry" if armed
                   else "WAIT — price in the zone but no reject yet (do NOT enter early; "
                        "that is where the 250-usd stop gets hit)"),
        "note": ob["note"],
    }


def optimus_scan(bars: Sequence, price: float) -> dict:
    """Scan every live zone, locate the OBs, gate on the reject, and anticipate the
    next zone in play — the full Optimus Prime read."""
    if not bars:
        return {"error": "no bars"}
    up = price >= LIVE_ZONES["pivots"].get("bullish_mean", price)
    reads = []
    for side in ("sell", "buy"):
        for z in LIVE_ZONES[side]:
            # only zones price can realistically be interacting with (within ~120 pips)
            near = (z["lo"] - 12 <= price <= z["hi"] + 12)
            pe = precision_entry(bars, z, side)
            pe["price_in_zone"] = bool(z["lo"] <= price <= z["hi"])
            pe["pips_away"] = _pips(price, (z["hi"] if side == "sell" else z["lo"]))
            if near or pe.get("armed"):
                reads.append(pe)
    armed = [r for r in reads if r.get("armed")]
    # anticipation: the next zone below (bearish) / above (bullish) from price
    direction = "sell" if not up else "buy"
    nxt = _next_target("sell" if direction == "sell" else "buy", price)
    ladder = target_ladder(direction, price)
    return {
        "price": round(price, 2), "bias_vs_mean": "below (sell)" if not up else "above",
        "armed": armed, "watching": [r for r in reads if not r.get("armed")],
        "next_zone": (nxt or {}).get("name"),
        "next_zone_band": [nxt["lo"], nxt["hi"]] if nxt else None,
        "target_ladder": ladder,
        "last_floor": ladder.get("last_floor"), "void_target": ladder.get("void_target"),
        "note": (f"{len(armed)} armed precision zone(s); "
                 + (f"anticipating {direction} into {(nxt or {}).get('name')}"
                    if nxt else "at a range extreme")
                 + ("; " + ladder["note"] if ladder.get("ladder") else "")),
    }


def format_optimus(scan: dict) -> Optional[str]:
    """Telegram card for an armed precision zone, else None."""
    armed = scan.get("armed") or []
    if not armed:
        return None
    lines = [f"🤖 *OPTIMUS PRIME — {scan['price']}*  ({scan.get('bias_vs_mean')})"]
    for r in armed:
        icon = "🔴" if r["side"] == "SELL" else "🟢"
        lines.append(f"{icon} *{r['side']}* {r['zone']}  OB {r['ob_zone']}")
        lines.append(f"   entry `{r['entry']}`  SL `{r['stop']}` ({r['risk_pips']:.0f} pips)")
        if r.get("target"):
            lines.append(f"   → {r['target_zone']} `{r['target']}`  "
                         f"({r['capture_pips']:.0f} pips · {r.get('capture_tier') or '—'} · {r.get('rr')}R)")
    if scan.get("next_zone"):
        lines.append(f"_next: {scan['next_zone']} {scan.get('next_zone_band')}_")
    return "\n".join(lines)
