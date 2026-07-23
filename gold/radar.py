"""HTF Order-Block radar — hack the OBs to cut manipulation, catch trend trades.

The order-block detector the trend book runs on: on the higher timeframe (daily /
weekly / monthly), the LAST up-close candle before a down displacement is a BEARISH
OB (supply); the last down-close before an up displacement is a BULLISH OB (demand).
The zone that matters is the candle's WICK + CLOSE area — where smart money reloads.

  bearish OB (supply) : zone [bottom, high]  → SELLS, TP down the ladder
  bullish OB (demand) : zone [low, top]      → BUYS,  TP up the ladder

Refinements:
  • MITIGATION — an OB is 'fresh/untapped' until price trades back into it. Fresh OBs
    are the highest-probability; radar_scan prefers them.
  • HTF BIAS — the most recent FRESH OB (across daily/weekly/monthly) sets the bias;
    while it's bullish we take more conviction on longs until the narrative shifts.
  • AUTO CONTINUITY — the TP ladder is derived from the fresh OB edges around price
    (falls back to the operator risk-book if none).
  • SWEEP+OB — the cleanest retest also sweeps liquidity first (ties to gold.rejection).
"""

from __future__ import annotations

from typing import List, Optional, Sequence

# Operator risk-book continuity fallback (default from 2026-07-22 chart).
CONTINUITY = {
    "sell": [4135.0, 4075.0, 4000.0, 3885.0],
    "buy": [4195.0, 4275.0, 4380.0],
}


def set_continuity(sell: Sequence[float] = None, buy: Sequence[float] = None) -> dict:
    if sell is not None:
        CONTINUITY["sell"] = sorted((float(x) for x in sell), reverse=True)
    if buy is not None:
        CONTINUITY["buy"] = sorted(float(x) for x in buy)
    return dict(CONTINUITY)


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]),
                float(bar["close"]), bar.get("time") or bar.get("date"))
    return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]),
            bar[0] if len(bar) else None)


def order_blocks(bars: Sequence, lookahead: int = 3, min_body_frac: float = 0.3,
                 timeframe: str = "1D") -> List[dict]:
    """Detect bullish/bearish HTF order blocks + track mitigation.

    An up-close candle becomes a BEARISH OB if a later candle (within ``lookahead``)
    CLOSES below its low; a down-close candle a BULLISH OB on the mirror. Each OB is
    tagged ``mitigated`` if any bar AFTER the confirming displacement traded back into
    its zone (fresh = untapped = highest probability)."""
    obs: List[dict] = []
    n = len(bars)
    for i in range(n - 1):
        o, h, l, c, t = _ohlc(bars[i])
        rng = h - l
        if rng <= 0 or abs(c - o) < min_body_frac * rng:
            continue
        up = c > o
        conf = None
        for j in range(i + 1, min(i + 1 + lookahead, n)):
            _o2, _h2, l2, c2, _t2 = _ohlc(bars[j])
            if up and c2 < l:
                conf = j
                zone = [round(min(o, c), 2), round(h, 2)]
                ob = {"type": "bearish", "kind": "supply", "timeframe": timeframe,
                      "top": round(h, 2), "bottom": round(min(o, c), 2),
                      "close": round(c, 2), "zone": zone, "formed": t}
                break
            if (not up) and c2 > h:
                conf = j
                zone = [round(l, 2), round(max(o, c), 2)]
                ob = {"type": "bullish", "kind": "demand", "timeframe": timeframe,
                      "top": round(max(o, c), 2), "bottom": round(l, 2),
                      "close": round(c, 2), "zone": zone, "formed": t}
                break
        if conf is None:
            continue
        # mitigation: any bar after the displacement re-enters the zone?
        lo, hi = ob["zone"]
        mitigated = any(_ohlc(bars[k])[2] <= hi and _ohlc(bars[k])[1] >= lo
                        for k in range(conf + 1, n))
        ob["mitigated"] = mitigated
        ob["fresh"] = not mitigated
        obs.append(ob)
    return obs


def unmitigated(obs: Sequence[dict]) -> List[dict]:
    return [o for o in obs if o.get("fresh")]


def htf_bias(obs: Sequence[dict]) -> dict:
    """The active structural bias from the most recent FRESH OB (bullish demand held
    below → longs; bearish supply held above → shorts)."""
    fresh = unmitigated(obs)
    if not fresh:
        return {"bias": "neutral", "note": "no fresh OB — wait for structure"}
    last = fresh[-1]
    bias = "long" if last["type"] == "bullish" else "short"
    return {"bias": bias, "ob": last,
            "note": f"fresh {last['type']} OB {last['zone']} ({last['timeframe']}) → "
                    f"conviction {bias.upper()} until the narrative shifts"}


def derive_continuity(obs: Sequence[dict], price: float, tol: float = 3.0) -> dict:
    """Auto-derive the TP ladder from fresh OB edges around price; fall back to the
    operator risk-book if there aren't enough OBs."""
    fresh = unmitigated(obs)
    downs = sorted({o["bottom"] for o in fresh if o["bottom"] < price - tol}, reverse=True)
    ups = sorted({o["top"] for o in fresh if o["top"] > price + tol})
    sells = downs[:4] if downs else [x for x in CONTINUITY["sell"] if x < price - tol]
    buys = ups[:4] if ups else [x for x in CONTINUITY["buy"] if x > price + tol]
    return {"sell_targets": sells, "buy_targets": buys,
            "source": "derived" if (downs or ups) else "risk_book"}


def active_retest(price: float, obs: Sequence[dict], tol: float = 3.0,
                  prefer_fresh: bool = True) -> Optional[dict]:
    """The OB price is retesting now — preferring fresh (untapped) blocks."""
    pool = unmitigated(obs) if (prefer_fresh and unmitigated(obs)) else list(obs)
    best = None
    for ob in pool:
        lo, hi = ob["zone"]
        if lo - tol <= price <= hi + tol:
            dist = 0.0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))
            if best is None or dist < best[0]:
                best = (dist, ob)
    return best[1] if best else None


def radar_scan(bars: Sequence, price: float, tol: float = 3.0,
               timeframe: str = "1D") -> dict:
    """Full OB radar: fresh/mitigated OBs, the retest, the HTF bias, and the derived
    continuity ladder."""
    obs = order_blocks(bars, timeframe=timeframe)
    active = active_retest(price, obs, tol)
    hb = htf_bias(obs)
    cont = derive_continuity(obs, price, tol)
    setup_bias = ("short" if active and active["type"] == "bearish"
                  else "long" if active else hb["bias"])
    return {
        "price": round(price, 2), "timeframe": timeframe,
        "order_blocks": obs[-10:], "fresh_count": len(unmitigated(obs)),
        "active_retest": active, "htf_bias": hb, "bias": setup_bias,
        "continuity": cont,
        "note": (f"retesting {'FRESH ' if active and active.get('fresh') else ''}"
                 f"{active['type']} OB {active['zone']} → hunt {setup_bias.upper()}s"
                 if active else f"HTF bias {hb['bias'].upper()} — {hb['note']}"),
    }


def sweep_ob_confluence(bars: Sequence, price: float, ob: dict, tol: float = 3.0) -> dict:
    """Cleanest entry: the OB retest ALSO swept liquidity first (a wick beyond the
    zone that closed back in). Ties into gold.rejection for the confirmation."""
    from gold.rejection import sweep_reject
    if not ob:
        return {"confluence": False, "reason": "no OB"}
    side = "short" if ob["type"] == "bearish" else "long"
    level = ob["top"] if side == "short" else ob["bottom"]
    rej = sweep_reject(bars, level, side, lookback=3)
    return {"confluence": bool(rej), "side": side, "level": level,
            "rejection": rej,
            "reason": (f"swept the OB {'top' if side=='short' else 'bottom'} "
                       f"{level} and rejected" if rej else "no sweep+reject at the OB yet")}


def breaker_blocks(bars: Sequence, obs: Sequence[dict] = None,
                   lookahead: int = 3) -> List[dict]:
    """Breaker blocks — an order block that FAILED and flipped polarity.

    When a bullish demand OB is broken (a later candle CLOSES below its bottom) it
    becomes a BEARISH breaker (former support → resistance); a broken bearish supply
    OB becomes a BULLISH breaker. The retest of a breaker is a high-probability
    continuation entry in the breaking direction.
    """
    if obs is None:
        obs = order_blocks(bars, lookahead=lookahead)
    n = len(bars)
    out: List[dict] = []
    for ob in obs:
        lo, hi = ob["zone"]
        broken_at = None
        for k in range(n):
            _o, _h, _l, c, _t = _ohlc(bars[k])
            if ob["type"] == "bullish" and c < lo:        # demand failed
                broken_at = k
                flip = "bearish"
                zone = [lo, hi]
                break
            if ob["type"] == "bearish" and c > hi:        # supply failed
                broken_at = k
                flip = "bullish"
                zone = [lo, hi]
                break
        if broken_at is None:
            continue
        # retested after the break?
        retested = any(_ohlc(bars[j])[2] <= hi and _ohlc(bars[j])[1] >= lo
                       for j in range(broken_at + 1, n))
        out.append({"type": flip, "kind": "breaker", "zone": zone,
                    "from": ob["type"], "timeframe": ob.get("timeframe", "1D"),
                    "broken_at": broken_at, "retested": retested, "fresh": not retested,
                    "note": f"{ob['type']} OB {zone} failed → {flip} breaker "
                            + ("(retested)" if retested else "(fresh)")})
    return out


# Institutional narrative cycle phases (the smart-money campaign clock).
def narrative_cycle(htf_bias: str, price: float, box=None,
                    swept: Optional[bool] = None) -> dict:
    """Label the current institutional phase — accumulation / manipulation /
    distribution / reaccumulation — from the HTF bias + where price sits in the
    range + whether liquidity was just swept. The 'narrative cycle' the desk trades
    around: accumulate the discount, manipulate (sweep) to trap, expand, distribute
    the premium."""
    disc = None
    if box is not None:
        disc = "discount" if price < box.mid else "premium"
    if htf_bias == "long":
        if disc == "discount":
            phase = "accumulation" if not swept else "manipulation"
            note = "smart money loading the discount" + (" (post-sweep spring)" if swept else "")
        else:
            phase = "distribution" if disc == "premium" else "expansion"
            note = "marking up into premium — watch for distribution"
    elif htf_bias == "short":
        if disc == "premium":
            phase = "distribution" if not swept else "manipulation"
            note = "offloading the premium" + (" (post-sweep trap)" if swept else "")
        else:
            phase = "reaccumulation" if disc == "discount" else "expansion"
            note = "marking down into discount"
    else:
        phase, note = "rebalance", "no directional narrative — two-way rotation"
    return {"phase": phase, "htf_bias": htf_bias, "location": disc,
            "swept": swept, "note": f"{phase.upper()} — {note}"}


def combine_htf(daily: Sequence[dict] = (), weekly: Sequence[dict] = (),
                monthly: Sequence[dict] = ()) -> dict:
    """Fuse the fresh-OB bias across daily / weekly / monthly. Higher timeframes
    carry more weight, so a fresh monthly demand OB dominates a daily supply one —
    the strategic map that validates a trade in higher timeframe."""
    reads = {"monthly": htf_bias(monthly), "weekly": htf_bias(weekly),
             "daily": htf_bias(daily)}
    weights = {"monthly": 3, "weekly": 2, "daily": 1}
    score = 0
    for tf, r in reads.items():
        if r["bias"] == "long":
            score += weights[tf]
        elif r["bias"] == "short":
            score -= weights[tf]
    bias = "long" if score > 0 else "short" if score < 0 else "neutral"
    return {"htf_bias": bias, "score": score, "by_tf": {tf: reads[tf]["bias"] for tf in reads},
            "note": (f"HTF ORB bias {bias.upper()} (M/W/D score {score:+d}) — "
                     "take conviction with the higher-timeframe order blocks")}


def fuse_with_weekly_profile(htf_bias_val: str, weekly_profile_bias: str) -> dict:
    """Combine the HTF OB bias with the weekly-profile direction into one conviction
    read — anticipate the momentum when both agree, stand down when they clash."""
    agree = htf_bias_val == weekly_profile_bias and htf_bias_val in ("long", "short")
    if agree:
        conviction = "high"
        note = f"HTF OB + weekly profile BOTH {htf_bias_val.upper()} — high-conviction trend"
    elif htf_bias_val == "neutral" or weekly_profile_bias in (None, "neutral"):
        conviction = "medium"
        note = "one leg neutral — trade the confirmed side, lighter size"
    else:
        conviction = "low"
        note = (f"HTF OB {htf_bias_val} vs weekly profile {weekly_profile_bias} — "
                "conflict, wait for alignment / narrative shift")
    return {"conviction": conviction, "htf_bias": htf_bias_val,
            "weekly_profile": weekly_profile_bias, "agree": agree, "note": note}


def format_radar(scan: dict) -> str:
    lines = [f"📡 *GOLD OB RADAR* — {scan['price']} ({scan['timeframe']})  ·  "
             f"{scan['fresh_count']} fresh"]
    a = scan.get("active_retest")
    if a:
        icon = "🔴" if a["type"] == "bearish" else "🟢"
        tag = "FRESH " if a.get("fresh") else "tapped "
        lines.append(f"{icon} retesting {tag}{a['type']} OB {a['zone']} "
                     f"(formed {a.get('formed')}) → hunt {scan['bias'].upper()}s")
    else:
        lines.append(f"⚪ HTF bias {scan['htf_bias']['bias'].upper()} — {scan['htf_bias']['note']}")
    c = scan["continuity"]
    if c["sell_targets"]:
        lines.append(f"↓ sell TPs ({c['source']}): " + " · ".join(f"{x:g}" for x in c["sell_targets"]))
    if c["buy_targets"]:
        lines.append(f"↑ buy TPs ({c['source']}): " + " · ".join(f"{x:g}" for x in c["buy_targets"]))
    return "\n".join(lines)
