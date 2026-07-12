"""Fibonacci structure engine — retracement, OTE, extension targets & invalidation SL.

This is the fix for the recurring *tight-stop* problem (stops half-an-SD away that
get wicked): a trade is framed off a real swing leg, the STOP sits beyond the swing
extreme (structure invalidation) — not a fixed pip count — and the TAKE-PROFITS are
fib EXTENSIONS of the same leg. The stop is as wide as the structure demands; the
money-first lot sizer (``gold.risk_engine.size_for_risk`` / USD/JPY sizing) then
keeps the *dollar* risk constant over that wider stop.

Pure and asset-agnostic. A "leg" is the impulse swing you measure, given as its
extremes ``(low, high)``. ``side`` names the trade you want to take on it:

  long   the leg is an up-impulse (low → high); you buy the DISCOUNT pullback,
         stop below the low, targets are extensions ABOVE the high.
  short  the leg is the up-swing being faded / a down-impulse (high → low); you
         sell the PREMIUM pullback, stop above the high (the "buyers exhaustion"),
         targets are extensions BELOW the low.

  ratios()                       the retracement / extension ratio ladders
  levels(low, high)              every retracement + extension price for a leg
  retracement(low, high, r)      one retracement price (r=0 → high, r=1 → low)
  extension(low, high, e, side)  one projected target price beyond the leg
  ote_zone(low, high, side)      the 0.618–0.786 optimal-entry band (+ 0.705 mid)
  invalidation(low, high, side, buffer)   the structural stop beyond the extreme
  plan_trade(low, high, side, …) full plan: entry + invalidation SL + ext TPs + R:R
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# Retracement ratios (fraction of the leg retraced back from its end).
#   0.0 == the leg's end (high for a long leg), 1.0 == its origin (the low).
#   0.618 / 0.705 / 0.786 are the OTE band; 0.705 is the sweet-spot entry.
RETRACEMENTS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 0.886, 1.0)

# Extension ratios (projection of the leg beyond itself) — the take-profit ladder.
EXTENSIONS = (1.272, 1.414, 1.618, 2.0, 2.618)

# The Optimal-Trade-Entry band and its mid.
_OTE = (0.618, 0.786)
_OTE_MID = 0.705


def ratios() -> Dict[str, tuple]:
    """The ratio ladders this engine uses (for docs / API introspection)."""
    return {"retracements": RETRACEMENTS, "extensions": EXTENSIONS,
            "ote": _OTE, "ote_mid": _OTE_MID}


def _norm(low: float, high: float) -> tuple:
    """Return (low, high, rng) with low<=high; rng>0 required by callers."""
    lo, hi = (low, high) if low <= high else (high, low)
    return lo, hi, hi - lo


def _is_long(side: str) -> bool:
    return side.lower() in ("long", "buy", "bull", "bullish")


def retracement(low: float, high: float, r: float) -> float:
    """One retracement price of the leg. ``r`` measured from the leg's END.

    r=0 → high, r=1 → low, r=0.705 → the 70.5% pullback. Direction-free: a
    retracement of an up-leg and the "premium pullback" of the same leg for a
    short are the same price, so both sides read off this.
    """
    lo, hi, rng = _norm(low, high)
    return round(hi - r * rng, 6)


def extension(low: float, high: float, e: float, side: str) -> float:
    """A projected target ``e`` beyond the leg, in the trade's direction.

    long  → above the high:  low + e*rng   (e=1.0 == the high, e=1.618 above it)
    short → below the low:    high - e*rng  (mirror, extends down)
    """
    lo, hi, rng = _norm(low, high)
    return round((lo + e * rng) if _is_long(side) else (hi - e * rng), 6)


def levels(low: float, high: float) -> Dict[str, Dict[str, float]]:
    """Every retracement and (upward) extension price of the leg, keyed by ratio."""
    lo, hi, rng = _norm(low, high)
    if rng <= 0:
        return {"retracements": {}, "extensions": {}}
    return {
        "retracements": {f"{r:.3f}": round(hi - r * rng, 6) for r in RETRACEMENTS},
        "extensions": {f"{e:.3f}": round(lo + e * rng, 6) for e in EXTENSIONS},
        "range": round(rng, 6),
    }


def ote_zone(low: float, high: float, side: str) -> Optional[dict]:
    """The 61.8–78.6% optimal-entry band + the 70.5% sweet-spot entry.

    long  → a DISCOUNT band (pullback down into the leg): high - r*rng
    short → a PREMIUM band (pullback up into the leg):     low + r*rng
    """
    lo, hi, rng = _norm(low, high)
    if rng <= 0:
        return None
    if _is_long(side):
        prices = {r: round(hi - r * rng, 6) for r in (_OTE[0], _OTE_MID, _OTE[1])}
    else:
        prices = {r: round(lo + r * rng, 6) for r in (_OTE[0], _OTE_MID, _OTE[1])}
    band = sorted((prices[_OTE[0]], prices[_OTE[1]]))
    return {"zone": [band[0], band[1]], "entry": prices[_OTE_MID],
            "levels": {f"{k:.3f}": v for k, v in prices.items()}}


def invalidation(low: float, high: float, side: str, buffer: float = 0.0) -> float:
    """The STRUCTURAL stop: just beyond the swing extreme, plus a buffer.

    This is the whole point — the stop lives where the trade THESIS is wrong
    (below the swing low for a long, above the swing high for a short), not at an
    arbitrary pip distance from entry. ``buffer`` pads for spread/wick noise.
    """
    lo, hi, _ = _norm(low, high)
    return round((lo - buffer) if _is_long(side) else (hi + buffer), 6)


def plan_trade(low: float, high: float, side: str,
               entry: Optional[float] = None, buffer: float = 0.0,
               ext_ratios: Sequence[float] = EXTENSIONS,
               min_rr: float = 3.0) -> dict:
    """A complete fib structure trade: OTE entry + invalidation SL + extension TPs.

    Entry defaults to the 70.5% OTE of the leg; pass ``entry`` to plan off a live
    fill. Returns per-target R:R and the RR to the first target, plus ``ok`` (the
    first target clears ``min_rr``). Sizing is left to the money-first lot layer —
    this only fixes WHERE the stop and targets sit.
    """
    lo, hi, rng = _norm(low, high)
    if rng <= 0:
        return {"ok": False, "reason": "degenerate leg (high == low)"}

    long = _is_long(side)
    ote = ote_zone(lo, hi, side)
    px = float(entry) if entry is not None else ote["entry"]
    stop = invalidation(lo, hi, side, buffer)
    risk = abs(px - stop)
    if risk <= 0:
        return {"ok": False, "reason": "entry is at/through the invalidation"}

    targets = []
    for e in ext_ratios:
        tp = extension(lo, hi, e, side)
        reward = (tp - px) if long else (px - tp)
        targets.append({"ratio": e, "price": tp,
                        "rr": round(reward / risk, 2)})
    # Keep only targets that are actually in profit (reward > 0), ordered outward.
    targets = [t for t in targets if t["rr"] > 0]

    first_rr = targets[0]["rr"] if targets else 0.0
    return {
        "ok": bool(targets) and first_rr >= min_rr,
        "side": "long" if long else "short",
        "entry": round(px, 6),
        "entry_mode": "structure" if entry is None else "given",
        "stop": stop,
        "risk": round(risk, 6),
        "stop_distance": round(risk, 6),
        "ote_zone": ote["zone"],
        "targets": targets,
        "rr_first": first_rr,
        "rr_max": targets[-1]["rr"] if targets else 0.0,
        "leg": {"low": round(lo, 6), "high": round(hi, 6), "range": round(rng, 6)},
        "reason": (None if (targets and first_rr >= min_rr)
                   else f"first target R:R {first_rr} < min {min_rr}"),
    }
