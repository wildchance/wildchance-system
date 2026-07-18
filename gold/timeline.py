"""HTF timeline identifier — the daily deviation ladder with named zones (pure).

The higher-timeframe map from the 1D chart: a 0.5-step standard-deviation ladder
anchored to a daily fib swing, where **0 = the anchor low ('central limit')** and
**1 = the anchor high**, projected up to +4 and down to −3. Each level carries a
SEMANTIC zone — central limit, bullish/bearish mean, buy/sell limit, equilibrium,
and the tp / scale-out targets — so `locate(price)` tells you which HTF zone price
is in and therefore which side to hunt on the smaller timeframe:

  discount (below the range) → look for LONGS intraday;
  premium (above equilibrium) → look for SHORTS / scale out;
  the pivot band → follow the trend.

Update HTF_ANCHOR (zero/one) when the daily swing that frames the year changes;
everything downstream is derived. Note: this HTF ladder is anchored 0=low (unlike
the intraday cbdr.sd_ladder which is 0=high) — it matches the 1D chart exactly.
"""

from __future__ import annotations

from typing import Dict, Optional

# Standing HTF anchor from the 1D chart (0 = low, 1 = high of the framing swing).
HTF_ANCHOR = {
    "zero": 3885.044,      # k=0  — central limit
    "one": 4381.940,       # k=1  — range high (the fib "1")
    "as_of": "2026-07-16",
    "source": "daily fib swing (TradingView 1D)",
}

# k → semantic zone name (symmetric scale-out structure).
_ZONE_NAMES: Dict[float, str] = {
    4.0: "tp4 / scale-out 3", 3.5: "tp3 / scale-out 2", 3.0: "tp2 / scale-out 1",
    2.5: "tp1", 2.0: "equilibrium (upper)", 1.5: "buy/sell limit (upper)",
    1.0: "range high", 0.5: "bullish mean", 0.0: "central limit",
    -0.5: "bearish mean", -1.0: "range low", -1.5: "buy/sell limit (lower)",
    -2.0: "equilibrium (lower)", -2.5: "tp1 (down)", -3.0: "tp1 / scale-out 1 (down)",
}


def _anchor(zero: Optional[float], one: Optional[float]):
    return (HTF_ANCHOR["zero"] if zero is None else zero,
            HTF_ANCHOR["one"] if one is None else one)


def htf_ladder(zero: Optional[float] = None, one: Optional[float] = None,
               up: float = 4.0, down: float = 3.0, step: float = 0.5) -> dict:
    """The full named HTF ladder: {k: {price, zone}} from −down to +up."""
    zero, one = _anchor(zero, one)
    unit = one - zero
    if unit <= 0:
        return {"zero": zero, "one": one, "unit": 0.0, "levels": {}}
    levels: Dict[str, dict] = {}
    k = -down
    while k <= up + 1e-9:
        levels[f"{k:g}"] = {"price": round(zero + k * unit, 3),
                            "zone": _ZONE_NAMES.get(round(k, 3))}
        k = round(k + step, 6)
    return {"zero": round(zero, 3), "one": round(one, 3), "unit": round(unit, 3),
            "levels": levels}


def _region_bias(k: float):
    """HTF region + the smaller-timeframe bias it implies."""
    if k >= 2.5:
        return "take-profit / distribution (premium)", "short", True
    if k >= 1.5:
        return "premium (buy/sell limit → equilibrium)", "short", False
    if k >= 0.5:
        return "upper pivot", "neutral", False
    if k >= -1.5:
        return "discount (accumulation)", "long", False
    if k >= -2.5:
        return "deep discount (→ equilibrium lower)", "long", False
    return "extreme discount / scale-out (down)", "long", True


def locate(price: float, zero: Optional[float] = None,
           one: Optional[float] = None) -> dict:
    """Where price sits on the HTF ladder: the k, the zone it's between, the region,
    the smaller-timeframe bias, and the nearest named levels above/below."""
    zero, one = _anchor(zero, one)
    unit = one - zero
    if unit <= 0:
        return {"price": price, "k": None, "region": "unknown", "smaller_tf_bias": "neutral"}
    k = (price - zero) / unit
    region, bias, scale = _region_bias(k)

    lad = htf_ladder(zero, one)["levels"]
    above = [(float(kk), v) for kk, v in lad.items() if v["price"] > price]
    below = [(float(kk), v) for kk, v in lad.items() if v["price"] <= price]
    nearest_above = min(above, key=lambda x: x[1]["price"] - price) if above else None
    nearest_below = max(below, key=lambda x: x[1]["price"]) if below else None

    return {
        "price": round(price, 3), "k": round(k, 3), "unit": round(unit, 3),
        "region": region, "smaller_tf_bias": bias, "at_scale_out": scale,
        "nearest_above": {"k": nearest_above[0], **nearest_above[1]} if nearest_above else None,
        "nearest_below": {"k": nearest_below[0], **nearest_below[1]} if nearest_below else None,
        "note": (f"HTF {region} (k={k:.2f}) — hunt {bias} setups on the smaller timeframe"
                 if bias != "neutral" else
                 f"HTF {region} (k={k:.2f}) — follow the trend on the smaller timeframe"),
    }


# --- weekly decision rule + cycle replication --------------------------------

# Fib subdivisions of the 0→1 anchor leg (the intermediate rail on the chart:
# 0.236=4002.3, 0.382=4074.9, 0.5=4133.5, 0.618=4192.1, 0.786=4275.6).
FIB_SUBS = (0.236, 0.382, 0.5, 0.618, 0.786)

# Weekly-close decision band around the 0.236 subdivision: a Friday close above
# the upper trigger buys to the 0.382; below the lower trigger sells to the zero
# (central limit). Between the triggers = no decision, wait for the close.
DECISION = {"buy_above": 4020.0, "sell_below": 4000.0}

# Next-cycle blueprint: once the current ladder is EXHAUSTED and price breaks &
# retests above the ATH (~5608), the same $-range blueprint replicates on the new
# swing. Anchored off the drawn monthly ladder (zero 5415.25 / one 5938.88 — unit
# ~$523): +4 of that cycle = ~8557; one further replication passes $10k.
NEXT_CYCLE = {"zero": 5415.252, "one": 5938.883, "source": "ATH breakout swing (1M chart)"}
ATH = 5608.35


def fib_levels(zero: Optional[float] = None, one: Optional[float] = None) -> Dict[str, float]:
    """The intermediate fib rail inside the 0→1 leg."""
    zero, one = _anchor(zero, one)
    u = one - zero
    return {f"{f:g}": round(zero + f * u, 3) for f in FIB_SUBS}


def weekly_decision(close: float, zero: Optional[float] = None,
                    one: Optional[float] = None) -> dict:
    """The Friday-close decision: above the trigger → long to the 0.382; below →
    short to the central limit; in between → stand aside until the close."""
    zero, one = _anchor(zero, one)
    fibs = fib_levels(zero, one)
    if close >= DECISION["buy_above"]:
        return {"decision": "long", "trigger": DECISION["buy_above"],
                "target": fibs["0.382"], "close": close,
                "note": f"weekly close {close} above {DECISION['buy_above']} → buy to {fibs['0.382']} (0.382)"}
    if close <= DECISION["sell_below"]:
        return {"decision": "short", "trigger": DECISION["sell_below"],
                "target": round(zero, 3), "close": close,
                "note": f"weekly close {close} below {DECISION['sell_below']} → sell to {zero} (central limit)"}
    return {"decision": "wait", "close": close,
            "band": [DECISION["sell_below"], DECISION["buy_above"]],
            "note": f"close {close} inside the {DECISION['sell_below']}–{DECISION['buy_above']} "
                    "decision band — wait for the weekly close"}


def cycle_status(price: float) -> dict:
    """Where the CURRENT cycle stands and when the blueprint replicates.

    exhausted   = price has reached the ladder's +4 extreme;
    break_retest= price holding above the ATH → the next-cycle ladder is live.
    """
    lad = htf_ladder()
    top = lad["levels"]["4"]["price"]
    if price > ATH:
        nxt = htf_ladder(NEXT_CYCLE["zero"], NEXT_CYCLE["one"])
        return {"phase": "next_cycle", "ath": ATH, "price": price,
                "next_cycle": {"anchor": NEXT_CYCLE, "unit": nxt["unit"],
                               "plus4": nxt["levels"]["4"]["price"]},
                "note": (f"above the {ATH} ATH — replicate the blueprint on the new swing "
                         f"(unit ~${nxt['unit']:.0f}, +4 ≈ {nxt['levels']['4']['price']:.0f}; "
                         "a further replication passes $10k)")}
    if price >= top:
        return {"phase": "exhausted", "top": top, "price": price,
                "note": f"ladder +4 ({top}) reached — watch for the break & retest above {ATH}"}
    return {"phase": "active", "top": top, "ath": ATH, "price": price,
            "note": "current cycle active — trade the ladder zones"}


# --- fractal subdivision + the daily mean-range map --------------------------

# The $25-step target collection ($25 … $250) walked from each day's mean.
TARGET_STEPS = (25, 50, 75, 100, 125, 150, 175, 200, 225, 250)


def fractal_units(zero: Optional[float] = None, one: Optional[float] = None) -> dict:
    """The timeline's fractal units. The named zones sit unit/2 apart (~$250 in
    gold); halving gives ~$125 and ~$62.5 — the lower-timeframe grids inside the
    same trend structure. Derived from the anchor, so they scale with the cycle."""
    zero, one = _anchor(zero, one)
    u = one - zero
    return {"range_250": round(u / 2, 3), "range_125": round(u / 4, 3),
            "range_62": round(u / 8, 3), "unit": round(u, 3)}


def daily_mean_map(day_open: float, day_close: float,
                   zero: Optional[float] = None, one: Optional[float] = None,
                   tol: float = 12.0) -> dict:
    """The daily mean-range map: mean = (open + close) / 2 of the closed daily
    candle, then the $25…$250 target ladder walked both ways from that mean —
    framed by the HTF structure (the aligned side is primary; targets that land on
    an HTF zone or fib level are flagged as confluence).
    """
    mean = round((day_open + day_close) / 2.0, 3)
    loc = locate(mean, zero, one)
    bias = loc["smaller_tf_bias"]

    # every mapped HTF price (ladder zones + fib rail) for confluence tagging
    zs, os_ = _anchor(zero, one)
    htf_prices = ([v["price"] for v in htf_ladder(zs, os_)["levels"].values()]
                  + list(fib_levels(zs, os_).values()))

    def _mk(side_price):
        near = min(htf_prices, key=lambda p: abs(p - side_price))
        conf = abs(near - side_price) <= tol
        return {"price": round(side_price, 2),
                "htf_level": round(near, 2) if conf else None}

    targets = []
    for s in TARGET_STEPS:
        targets.append({"usd": s, "up": _mk(mean + s), "down": _mk(mean - s),
                        "primary": "up" if bias == "long" else
                                   "down" if bias == "short" else "both"})
    return {
        "mean": mean, "open": day_open, "close": day_close,
        "htf": {"k": loc["k"], "region": loc["region"], "bias": bias},
        "fractal": fractal_units(zero, one),
        "targets": targets,
        "note": (f"daily mean {mean} in {loc['region']} → primary side "
                 f"{'UP' if bias == 'long' else 'DOWN' if bias == 'short' else 'both'}; "
                 "$25-step collection to ±$250, HTF-confluent targets flagged"),
    }


def htf_confluence(side: str, price: float, zero: Optional[float] = None,
                   one: Optional[float] = None) -> str:
    """'aligns' / 'opposes' / 'neutral' for a smaller-TF side vs the HTF region."""
    want = "long" if side.lower() in ("long", "buy") else "short"
    bias = locate(price, zero, one)["smaller_tf_bias"]
    if bias == "neutral":
        return "neutral"
    return "aligns" if bias == want else "opposes"
