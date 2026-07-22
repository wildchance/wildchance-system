"""HTF Order-Block radar — hack the OBs to cut manipulation, catch trend trades.

The order-block detector the trend book runs on: on the higher timeframe (daily+),
the LAST up-close candle before a down displacement is a BEARISH OB (supply); the
last down-close candle before an up displacement is a BULLISH OB (demand). The zone
that matters is the candle's WICK + CLOSE area — where smart money reloads and price
gets manipulated. Retesting an unmitigated OB is the high-probability continuation
entry (e.g. the 6-Jul daily OB retested today), and the continuity TP ladder (the
risk book) names the take-profits price runs between.

  bearish OB (supply) : zone [close, high]  → look for SELLS, TP down the ladder
  bullish OB (demand) : zone [low, close]   → look for BUYS,  TP up the ladder

Feed daily (or 4H) OHLC bars oldest-first; get the OBs, which one price is retesting,
and the continuity targets split around price.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

# The risk-book continuity take-profits (operator-set; default from 2026-07-22 chart).
CONTINUITY = {
    "sell": [4135.0, 4075.0, 4000.0, 3885.0],
    "buy": [4195.0, 4275.0, 4380.0],
}


def set_continuity(sell: Sequence[float] = None, buy: Sequence[float] = None) -> dict:
    """Operator-set the continuity TP ladder (the risk book)."""
    if sell is not None:
        CONTINUITY["sell"] = sorted((float(x) for x in sell), reverse=True)
    if buy is not None:
        CONTINUITY["buy"] = sorted(float(x) for x in buy)
    return dict(CONTINUITY)


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]),
                float(bar["close"]), bar.get("time") or bar.get("date"))
    # (date, o, h, l, c)
    return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]),
            bar[0] if len(bar) else None)


def order_blocks(bars: Sequence, lookahead: int = 3, min_body_frac: float = 0.3) -> List[dict]:
    """Detect bullish/bearish HTF order blocks with a displacement confirmation.

    An up-close candle becomes a BEARISH OB if a later candle (within ``lookahead``)
    CLOSES below its low; a down-close candle a BULLISH OB if a later close breaks
    above its high. The OB zone is the wick+close area on the reloading side.
    """
    obs: List[dict] = []
    n = len(bars)
    for i in range(n - 1):
        o, h, l, c, t = _ohlc(bars[i])
        rng = h - l
        if rng <= 0 or abs(c - o) < min_body_frac * rng:
            continue                                   # skip dojis / weak bodies
        up = c > o
        for j in range(i + 1, min(i + 1 + lookahead, n)):
            _o2, _h2, l2, c2, _t2 = _ohlc(bars[j])
            if up and c2 < l:                          # down displacement → bearish OB
                obs.append({"type": "bearish", "kind": "supply",
                            "top": round(h, 2), "bottom": round(min(o, c), 2),
                            "close": round(c, 2), "wick": round(h, 2),
                            "zone": [round(min(o, c), 2), round(h, 2)],
                            "formed": t, "confirmed_by": _t2})
                break
            if (not up) and c2 > h:                     # up displacement → bullish OB
                obs.append({"type": "bullish", "kind": "demand",
                            "top": round(max(o, c), 2), "bottom": round(l, 2),
                            "close": round(c, 2), "wick": round(l, 2),
                            "zone": [round(l, 2), round(max(o, c), 2)],
                            "formed": t, "confirmed_by": _t2})
                break
    return obs


def active_retest(price: float, obs: Sequence[dict], tol: float = 3.0) -> Optional[dict]:
    """The OB price is currently retesting (inside the zone or within ``tol``)."""
    best = None
    for ob in obs:
        lo, hi = ob["zone"]
        if lo - tol <= price <= hi + tol:
            dist = 0.0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))
            if best is None or dist < best[0]:
                best = (dist, ob)
    return best[1] if best else None


def radar_scan(bars: Sequence, price: float, tol: float = 3.0) -> dict:
    """The full OB radar: order blocks, the one being retested now, the trade bias
    it implies, and the continuity TP ladder split around price."""
    obs = order_blocks(bars)
    active = active_retest(price, obs, tol)
    bias = None
    if active:
        bias = "short" if active["type"] == "bearish" else "long"
    sells = [x for x in CONTINUITY["sell"] if x < price - tol]
    buys = [x for x in CONTINUITY["buy"] if x > price + tol]
    return {
        "price": round(price, 2),
        "order_blocks": obs[-8:],                      # most recent
        "active_retest": active,
        "bias": bias,
        "continuity": {"sell_targets": sells, "buy_targets": buys},
        "note": (f"retesting a {active['type']} OB {active['zone']} → hunt {bias.upper()}s"
                 if active else "no OB retest — price between blocks"),
    }


def format_radar(scan: dict) -> str:
    """Telegram card for the OB radar."""
    lines = [f"📡 *GOLD OB RADAR* — {scan['price']}"]
    a = scan.get("active_retest")
    if a:
        icon = "🔴" if a["type"] == "bearish" else "🟢"
        lines.append(f"{icon} retesting {a['type']} OB {a['zone']} (formed {a.get('formed')}) "
                     f"→ hunt {scan['bias'].upper()}s")
    else:
        lines.append("⚪ no OB retest — price between blocks")
    c = scan["continuity"]
    if c["sell_targets"]:
        lines.append("↓ sell TPs: " + " · ".join(f"{x:g}" for x in c["sell_targets"]))
    if c["buy_targets"]:
        lines.append("↑ buy TPs: " + " · ".join(f"{x:g}" for x in c["buy_targets"]))
    return "\n".join(lines)
