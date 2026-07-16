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


def htf_confluence(side: str, price: float, zero: Optional[float] = None,
                   one: Optional[float] = None) -> str:
    """'aligns' / 'opposes' / 'neutral' for a smaller-TF side vs the HTF region."""
    want = "long" if side.lower() in ("long", "buy") else "short"
    bias = locate(price, zero, one)["smaller_tf_bias"]
    if bias == "neutral":
        return "neutral"
    return "aligns" if bias == want else "opposes"
