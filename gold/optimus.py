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
  4. ANTICIPATE — the next zone in the trade direction. 2026-07-28 live read: price
     bounced to ~4049 and BUYS the retrace into the 4074 Daily OB; there the primary-
     trend SELL re-arms down to 3885 (central limit), then the W1 void to ~3506.

The live reaction map is operator-fed (set_live_zones / set_sell_path / set_campaign)
and defaults to the 2026-07-28 read. Reuses gold.rejection + gold.big5 (capture grade).
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from gold.big5 import tier_for_pips, MIN_CAPTURE_PIPS
from gold.risk_engine import GOLD_PIP

_STOP_BUFFER = 1.5      # stop beyond the OB wick, in price

# Live reaction map — 2026-07-24 4H chart. Operator-updatable via set_live_zones().
LIVE_ZONES = {
    "sell": [
        {"name": "supply_4152_4163", "lo": 4152.40, "hi": 4163.07, "note": "up/down-close supply"},
        {"name": "supply_4108_4110", "lo": 4108.00, "hi": 4110.05,
         "note": "recent sell origin (2026-07-28)"},
        {"name": "ob_4075_4094", "lo": 4074.86, "hi": 4094.03,
         "note": "Daily OB + 4H supply — the bounce buy-target AND where the SELL re-arms"},
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
    "pivots": {"bullish_mean": 4133.90, "daily_sell_ob": 4200.0, "sweep_level": 4135.0,
               "buy_ob_4h": 4033.0, "sell_target": 3885.04, "july_high": 4200.0,
               "active_sell_ob": 4200.0, "bounce_from": 4033.0},
}

# A gap larger than this (in pips) with no zone = a no-floor VOID: price travels it
# fast, so the efficient target is the far side, not an imaginary level inside it.
VOID_MIN_PIPS = 2000.0        # 2000 pips = 200 usd of air

# The HTF fib / structure map (Daily+4H, 2026-07-24) — the macro→micro frame that
# locks the trend to 2027. Operator-updatable via set_fib_map().
FIB_MAP = {
    "equilibrium": 4877.98,          # 2.0
    "buy_sell_limit_upper": 4629.35,  # 1.5
    "premium_1": 4381.94,            # 1.0
    "fib_0786": 4275.60,
    "fib_0618": 4192.13,
    "bullish_mean": 4133.90,         # 0.5 — broke & retested
    "fib_0382": 4074.86,             # next break-retest sell
    "fib_0236": 4002.31,             # break-retest continue selling
    "central_limit": 3885.04,        # 0.0 — last floor before the void
    "bearish_mean": 3635.49,         # -0.5
    "buy_sell_limit_1": 3389.34,     # -1.0  (3506/3390 bullish OB)
    "buy_sell_limit_15": 3131.46,    # -1.5  (3291/3130 bullish OB)
}
# Premium levels that act as SELL-on-retest in the bearish structure (broken support
# → resistance). Ordered high→low; each is a sell-limit on the retest.
# Aug/Sep monthly frame: 4200 = July high / Daily sell OB (Aug sweeps it → Sep
# distributes). 4135 = the high-sweep / failed-sweep trigger for the sells to 3885.
SELL_RETEST_LEVELS = [4200.00, 4163.07, 4152.40, 4135.00, 4110.05, 4094.03,
                      4074.86, 4002.31]

# Which timeframe order-block each retest level is (precision labelling). 4074 =
# Daily OB, 4135 = 4H OB per the operator's 2026-07-24 read.
OB_TIMEFRAME = {
    4200.00: "Daily sell OB — July high (Aug sweep → Sep distribution)",
    4163.07: "4H supply — sell-off origin", 4152.40: "4H supply",
    4135.00: "high-sweep / failed-sweep trigger → sells to 3885",
    4110.05: "recent sell origin", 4094.03: "4H supply",
    4074.86: "4H supply", 4033.00: "4H order block — the BUY (limit)",
    4002.31: "0.236 shelf / break-retest",
}


def _ob_tf(level: float, tol: float = 4.0) -> Optional[str]:
    best = None
    for lv, tf in OB_TIMEFRAME.items():
        d = abs(level - lv)
        if d <= tol and (best is None or d < best[1]):
            best = (tf, d)
    return best[0] if best else None

# Real-time journaling — the expected trade pool over the campaign to 3130, by $-tier.
CAMPAIGN = {
    "from": 4163.07, "target": 3131.46,
    "macro_legs_250usd": 4,               # 4 × 250-usd structural legs to 3130
    # The leg being worked right now (2026-07-28): buy the 4049→4075 retrace, then the
    # SELL re-arms at the 4074 Daily OB down to 3885. Operator-updatable via set_campaign().
    "active_leg": {
        "from": 4110.05, "buy_bounce_to": 4074.86, "sell_target": 3885.04,
        "trades_so_far": 5, "retracement_trades": 2,
        "note": "leg 4110→3885; currently the 2nd retracement — buy 4049→4075, then sell 4075→3885",
    },
    "micro_tiers": {
        "50usd":        {"min": 60, "max": 120},
        "125usd":       {"min": 7,  "max": 8},
        "150usd":       {"min": 24, "max": 48},
        "mixed_50_150": {"min": 36, "max": 72},   # prop-account leverage pool
    },
    "note": "counts set by real-time opportunity; prop accs leverage the mixed pool",
}


def set_campaign(from_: float = None, buy_bounce_to: float = None, sell_target: float = None,
                 trades_so_far: int = None, retracement_trades: int = None) -> dict:
    """Operator update of the ACTIVE campaign leg (today's buy-bounce / sell-target /
    trade counters) — so the campaign read tracks the live trades without a redeploy."""
    leg = CAMPAIGN.setdefault("active_leg", {})
    for k, v in (("from", from_), ("buy_bounce_to", buy_bounce_to), ("sell_target", sell_target),
                 ("trades_so_far", trades_so_far), ("retracement_trades", retracement_trades)):
        if v is not None:
            leg[k] = v
    return dict(leg)


def set_fib_map(**levels) -> dict:
    """Operator update of the HTF fib/structure map (feed today's levels)."""
    for k, v in levels.items():
        if v is not None:
            try:
                FIB_MAP[k] = float(v)
            except (TypeError, ValueError):
                pass
    return dict(FIB_MAP)


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


def _cbdr_confluence(level: float, box, tol: float = 6.0) -> Optional[str]:
    """Does a level line up with a pre-London CBDR ±SD projection? (highest precision)."""
    if box is None:
        return None
    best = None
    for name, lv in (getattr(box, "levels", {}) or {}).items():
        if lv is not None and abs(level - lv) <= tol:
            d = abs(level - lv)
            if best is None or d < best[1]:
                best = (name, d)
    return best[0] if best else None


def sell_limit_ladder(price: float, box=None, floor: Optional[float] = None) -> dict:
    """Pinpoint the SELL-LIMIT levels — the break-retest structure. Each premium level
    above the floor becomes a sell-limit on the retest: entry at the level, stop above
    the next-higher level, target the next level down (finally the central-limit floor).
    Tags CBDR confluence when a pre-London ±SD projection lines up (highest precision)."""
    fl = floor if floor is not None else FIB_MAP["central_limit"]
    levels = sorted((l for l in SELL_RETEST_LEVELS if l > fl), reverse=True)
    ladder = []
    for i, lvl in enumerate(levels):
        higher = levels[i - 1] if i > 0 else None
        stop = round((higher + _STOP_BUFFER) if higher else lvl * 1.004, 2)
        lower = [l for l in levels if l < lvl]
        target = round(lower[0] if lower else fl, 2)
        dist = abs(lvl - stop) or 1e-9
        cap = _pips(lvl, target)
        tier = tier_for_pips(cap)
        ladder.append({
            "sell_limit": round(lvl, 2), "stop": stop, "target": target,
            "risk_pips": _pips(lvl, stop), "capture_pips": cap,
            "tier": (tier or {}).get("name"),
            "rr": round(abs(lvl - target) / dist, 2),
            "status": ("live retest" if lvl <= price + 5 else "armed above"),
            "ob_timeframe": _ob_tf(lvl),
            "cbdr": _cbdr_confluence(lvl, box),
        })
    live = [r for r in ladder if r["status"] == "live retest"]
    return {"price": round(price, 2), "floor": fl, "sell_limits": ladder,
            "nearest_live": live[0] if live else None,
            "note": (f"{len(ladder)} sell-limits mapped to {fl:.0f}; "
                     + (f"nearest live retest {live[0]['sell_limit']}" if live
                        else "price below all premium retests — ride the trend"))}


# H4 sell-anticipation STAIRCASE (2026-07-24 operator chart) — the alternating
# retrace(lower-high) / impulse(lower-low) path down to TP. Operator-updatable.
# 2026-07-28 sell staircase — price bounced to ~4049 and buys the retrace into the
# 4074 Daily OB, then the primary-trend SELL re-arms down to 3885 (central limit).
# Below 3885 is the W1 no-floor VOID: nothing real until ~3506 (the next campaign leg).
# Aug/Sep sell staircase: buy the 4033 4H OB up into the 4200 Daily sell OB (July
# high), then the SELL re-arms — 4200 → 4135 sweep → down to 3885 = the 2500-pip leg.
PATH_SELLS = [4200.00, 4163.07, 4135.00, 4110.05, 4074.86]   # sell OBs (lower-highs)
PATH_FLOORS = [4002.31, 3885.04]                             # demand (lower-lows) before the void
PATH_TP = 3885.04                                            # target; void below → 3506 next leg


def set_sell_path(sells=None, floors=None, tp=None) -> dict:
    """Operator update of the H4 sell-anticipation staircase."""
    global PATH_SELLS, PATH_FLOORS, PATH_TP
    if sells is not None:
        PATH_SELLS = sorted((float(x) for x in sells), reverse=True)
    if floors is not None:
        PATH_FLOORS = sorted((float(x) for x in floors), reverse=True)
    if tp is not None:
        PATH_TP = float(tp)
    return {"sells": PATH_SELLS, "floors": PATH_FLOORS, "tp": PATH_TP}


def _leg(n: int, kind: str, frm: float, to: float, action: str) -> dict:
    pips = _pips(frm, to)
    tier = tier_for_pips(pips)
    return {"leg": n, "type": kind, "from": round(frm, 2), "to": round(to, 2),
            "pips": pips, "tier": (tier or {}).get("name"), "action": action}


def sell_path(price: float, tp: Optional[float] = None) -> dict:
    """Project the sequenced sell staircase — alternating retrace-up (into a lower-high
    sell OB) and impulse-down (to the next demand) — down to TP. Turns the zone ladder
    into the roadmap you drew: each impulse leg is a big trade, each retrace a re-sell."""
    tp = tp if tp is not None else PATH_TP
    lhs_above = sorted((l for l in PATH_SELLS if l >= price * 0.999))
    floors = sorted({f for f in PATH_FLOORS if f < price and f > tp} | {tp}, reverse=True)
    legs, cur, n = [], float(price), 1
    prev_lh = cur
    # initial retrace into the nearest sell OB (the lower-high the bounce sells from)
    if lhs_above:
        lh = lhs_above[0]
        legs.append(_leg(n, "retrace_up", cur, lh, "buy the bounce into the sell OB")); n += 1
        cur, prev_lh = lh, lh
    for fl in floors:
        legs.append(_leg(n, "impulse_down", cur, fl, f"SELL to demand {fl:.0f}")); n += 1
        cur = fl
        if fl <= tp:
            break
        # bounce to a LOWER high — the nearest level between this floor and the last LH
        cands = [l for l in (PATH_SELLS + PATH_FLOORS) if fl < l < prev_lh]
        blh = max(cands) if cands else round(fl + (prev_lh - fl) * 0.5, 2)
        blh = min(blh, prev_lh - 1.0)
        legs.append(_leg(n, "retrace_up", cur, blh, "bounce (lower high) → re-sell")); n += 1
        cur, prev_lh = blh, blh
    sell_legs = [l for l in legs if l["type"] == "impulse_down"]
    return {
        "price": round(price, 2), "tp": tp, "structure": "lower-highs / lower-lows staircase",
        "legs": legs, "sell_legs": len(sell_legs),
        "retraces": len([l for l in legs if l["type"] == "retrace_up"]),
        "total_pips": _pips(price, tp),
        "note": (f"{len(sell_legs)} sell legs to TP {tp:.0f} — each impulse is a big "
                 "trade, each retrace a 50/150-usd re-sell (the staircase feeds the "
                 "campaign trade-count)"),
    }


def bounce_plan(price: float, box=None, n: int = 3) -> dict:
    """Counter-trend BOUNCE map — when price retraces UP toward premium OBs, those OBs
    are the buy TARGETS *and* where the primary-trend SELL re-arms. Buy the bounce into
    the OB, then sell the OB. Feeds the daily 4074 / 4H 4135 read directly."""
    above = sorted((l for l in SELL_RETEST_LEVELS if l > price))[:n]
    targets = []
    for lvl in above:
        low_after = [l for l in SELL_RETEST_LEVELS if l < lvl] + [FIB_MAP["central_limit"]]
        sell_target = max([l for l in low_after if l < lvl], default=FIB_MAP["central_limit"])
        targets.append({
            "level": round(lvl, 2), "ob": _ob_tf(lvl),
            "pips_up": _pips(price, lvl),
            "sell_rearm": {"entry": round(lvl, 2), "target": round(sell_target, 2),
                           "capture_pips": _pips(lvl, sell_target),
                           "tier": (tier_for_pips(_pips(lvl, sell_target)) or {}).get("name")},
            "cbdr": _cbdr_confluence(lvl, box),
        })
    return {"price": round(price, 2), "bias": "counter-trend bounce (buy → sell the OB)",
            "buy_targets": targets,
            "note": ("buy the bounce into the OB, then SELL it — primary trend down. "
                     + (f"nearest: {targets[0]['ob'] or 'level'} @ {targets[0]['level']} "
                        f"(+{targets[0]['pips_up']:.0f} pips)" if targets
                        else "no premium OB above — price at a premium extreme"))}


def campaign_projection(price: Optional[float] = None) -> dict:
    """The real-time journaling structure — expected trades per $-tier over the
    campaign to 3130, plus progress from the current price."""
    total_usd = round(CAMPAIGN["from"] - CAMPAIGN["target"], 2)
    done = round(CAMPAIGN["from"] - price, 2) if price else None
    pct = round(done / total_usd * 100, 1) if (done and total_usd) else None
    tiers = {}
    for name, band in CAMPAIGN["micro_tiers"].items():
        tiers[name] = {**band, "midpoint": (band["min"] + band["max"]) // 2}
    return {
        "campaign": f"{CAMPAIGN['from']:.0f} → {CAMPAIGN['target']:.0f}",
        "total_move_usd": total_usd, "total_move_pips": _pips(CAMPAIGN["from"], CAMPAIGN["target"]),
        "macro_legs_250usd": CAMPAIGN["macro_legs_250usd"],
        "active_leg": CAMPAIGN.get("active_leg"),
        "micro_tiers": tiers, "progress_usd": done, "progress_pct": pct,
        "note": (f"{CAMPAIGN['macro_legs_250usd']}×250-usd macro legs to "
                 f"{CAMPAIGN['target']:.0f}"
                 + (f"; {pct}% travelled" if pct is not None else "")
                 + f" — {CAMPAIGN['note']}"),
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
