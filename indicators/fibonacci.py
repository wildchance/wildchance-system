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

Trend-based (3-point A→B→C) extension — anticipate the continuation target zones:
  trend_extension(a, b, c, r)    one projected price:  C + r·(B−A)
  trend_levels(a, b, c)          full projection ladder + reaction/target zones
  trend_plan(a, b, c, side, …)   enter at C, stop at invalidation, TP the extensions
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


# ─────────────────────────────────────────────────────────────────────────────
# TREND-BASED (3-point A→B→C) fib extension — anticipate the target/extension zones
# ─────────────────────────────────────────────────────────────────────────────
# Two-point ``extension`` projects off a single leg. The trend-based tool (the
# TenCapital / TradingView "Trend-Based Fib Extension") uses THREE points — an
# impulse A→B and its retracement to C — and projects the ladder FROM C by the
# size of the A→B leg. That is what anticipates *where a continuation runs to*:
#
#   price(r) = C + r · (B − A)
#
# r=0 sits at C, r=1.0 is a full measured move (equal leg) off the pullback, and
# 1.618 / 2.618 are the golden continuation zones. A negative r projects the
# other way (the −4.16 ≈ downside target on the gold chart).

# The projection ladder. 1.0 = measured move; 0.618–1.0 = first reaction zone;
# 1.272–1.618 = the golden continuation (target) zone; 2.0+ = extended runners.
TREND_EXTENSIONS = (0.0, 0.382, 0.5, 0.618, 0.786, 1.0,
                    1.272, 1.414, 1.618, 2.0, 2.618, 3.618, 4.236)

# The two zones a trader actually aims at, as (lo_ratio, hi_ratio).
_REACTION_ZONE = (0.618, 1.0)      # first measured-move reaction
_TARGET_ZONE = (1.272, 1.618)      # golden continuation / primary target


def trend_extension(a: float, b: float, c: float, r: float) -> float:
    """One trend-based extension price: ``C + r·(B−A)`` (projected from C)."""
    return round(c + r * (b - a), 6)


def trend_levels(a: float, b: float, c: float,
                 ratios: Sequence[float] = TREND_EXTENSIONS) -> dict:
    """The full A→B→C projection ladder, plus the reaction & target zones.

    ``side`` is inferred from the A→B leg: B>A is an up-impulse (continuation
    long), B<A a down-impulse (continuation short). ``zones`` are the price bands
    to watch for reactions / take-profits.
    """
    leg = b - a
    if leg == 0:
        return {"side": None, "leg": 0.0, "levels": {}, "zones": {}}
    long = leg > 0
    prices = {f"{r:.3f}": round(c + r * leg, 6) for r in ratios}

    def _zone(z):
        lo = round(c + z[0] * leg, 6)
        hi = round(c + z[1] * leg, 6)
        return sorted((lo, hi))

    return {
        "side": "long" if long else "short",
        "leg": round(leg, 6),
        "anchor": round(c, 6),
        "levels": prices,
        "zones": {"reaction": _zone(_REACTION_ZONE), "target": _zone(_TARGET_ZONE)},
    }


def trend_plan(a: float, b: float, c: float, side: Optional[str] = None,
               entry: Optional[float] = None, buffer: float = 0.0,
               stop_ref: str = "c", targets: Sequence[float] = (1.0, 1.618, 2.618),
               min_rr: float = 2.0) -> dict:
    """A continuation trade off the A→B→C swing: enter at C, target the extensions.

    The pullback INTO C is the entry; the stop sits at structure invalidation —
    beyond C (``stop_ref='c'``, the pullback is over) or beyond A (``stop_ref='a'``,
    the whole leg is invalid). Take-profits are the trend extensions, so the trade
    aims straight at the anticipated continuation zones. ``side`` is inferred from
    the leg unless forced.
    """
    leg = b - a
    if leg == 0:
        return {"ok": False, "reason": "degenerate impulse (A == B)"}
    long = (leg > 0) if side is None else _is_long(side)

    px = float(entry) if entry is not None else round(c, 6)
    # Invalidation: below the anchor for a long, above it for a short.
    ref = c if stop_ref == "c" else a
    stop = round((ref - buffer) if long else (ref + buffer), 6)
    risk = abs(px - stop)
    if risk <= 0:
        return {"ok": False, "reason": "entry is at/through the invalidation"}

    tps = []
    for r in targets:
        tp = round(c + (r if long else -r) * abs(leg), 6)
        reward = (tp - px) if long else (px - tp)
        tps.append({"ratio": r, "price": tp, "rr": round(reward / risk, 2)})
    tps = [t for t in tps if t["rr"] > 0]

    first_rr = tps[0]["rr"] if tps else 0.0
    lv = trend_levels(a, b, c)
    return {
        "ok": bool(tps) and first_rr >= min_rr,
        "kind": "trend-extension",
        "side": "long" if long else "short",
        "entry": px,
        "entry_mode": "anchor(C)" if entry is None else "given",
        "stop": stop,
        "stop_ref": stop_ref,
        "risk": round(risk, 6),
        "stop_distance": round(risk, 6),
        "targets": tps,
        "rr_first": first_rr,
        "rr_max": tps[-1]["rr"] if tps else 0.0,
        "zones": lv["zones"],
        "abc": {"a": round(a, 6), "b": round(b, 6), "c": round(c, 6),
                "leg": round(leg, 6)},
        "reason": (None if (tps and first_rr >= min_rr)
                   else f"first target R:R {first_rr} < min {min_rr}"),
    }
