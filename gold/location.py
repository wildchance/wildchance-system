"""Premium / discount entry-location gate (pure, stdlib-only).

The single structural fix behind "every trade loses except the weekly open": the
old engine market-filled at the LIVE price, so continuation profiles bought
*premium* (the expensive upper half) and sold *discount* (the cheap lower half) —
chasing the move right before it retraced. ICT is the opposite: buy discount,
sell premium.

This module scores where a price sits inside a reference range (the session /
weekly range, or an OTE leg) and answers ONE question: is this a valid entry
LOCATION for the proposed side? Direction still comes from the weekly + macro
bias; this only vetoes entering at the wrong side of the range.

  equilibrium = (high + low) / 2
  pos         = (price − low) / (high − low)     0 at the low … 1 at the high
  discount    = lower half (pos < 0.5)  → the place to BUY
  premium     = upper half (pos ≥ 0.5)  → the place to SELL

`deep` (default 0.5) is the fraction of the range that still counts as a valid
entry: 0.5 = anywhere in the correct half; tighten toward the −1SD/OTE style
"deep discount" by lowering it (e.g. 0.33 = only the cheapest third for a long).
"""

from __future__ import annotations

from typing import Optional


def discount_premium(price: float, high: float, low: float) -> dict:
    """Where a price sits inside [low, high]: pos, zone, equilibrium."""
    rng = high - low
    if rng <= 0:
        return {"pos": None, "zone": "unknown", "equilibrium": high,
                "high": high, "low": low}
    pos = (price - low) / rng
    return {
        "pos": round(pos, 4),
        "zone": "premium" if pos >= 0.5 else "discount",
        "equilibrium": round((high + low) / 2.0, 4),
        "high": high, "low": low,
    }


def location_gate(side: str, price: float, high: float, low: float,
                  deep: float = 0.5) -> dict:
    """Is `price` a valid entry LOCATION for `side` inside [low, high]?

    Longs must be in discount (pos ≤ deep); shorts must be in premium
    (pos ≥ 1 − deep). Returns {ok, reason, zone, pos, equilibrium, ...}.
    """
    dp = discount_premium(price, high, low)
    if dp["pos"] is None:
        # No usable range (flat/degenerate) — don't block on location.
        return {"ok": True, "reason": "no reference range — location gate skipped",
                **dp}

    long = side.lower() in ("long", "buy")
    pos = dp["pos"]
    if long:
        ok = pos <= deep
        want = f"discount (pos ≤ {deep:g})"
    else:
        ok = pos >= (1.0 - deep)
        want = f"premium (pos ≥ {1.0 - deep:g})"

    if ok:
        reason = f"entry in {dp['zone']} (pos {pos:.2f}) — valid {('buy' if long else 'sell')} location"
    else:
        reason = (f"price in {dp['zone']} (pos {pos:.2f}); a "
                  f"{'long' if long else 'short'} needs {want} — no chasing, wait for the pullback")
    return {"ok": ok, "reason": reason, "want": want, **dp}


def in_zone(price: Optional[float], zone: Optional[list]) -> bool:
    """Is price within an explicit [lo, hi] zone (e.g. the OTE band)?"""
    if price is None or not zone or len(zone) != 2:
        return False
    lo, hi = sorted(zone)
    return lo <= price <= hi
