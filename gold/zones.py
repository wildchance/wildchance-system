"""Named order-block zones + sniper entry layering + zone-to-zone pip budget (pure).

The weekly/daily fib map (TradingView, 2026-07-18) resolves into a small set of
NAMED order-block zones — the "recon targets" price hunts between. Instead of a
single limit price, each zone is worked with a SNIPER STACK: 2–3 layered limits
spread across the zone band with one shared stop just beyond it, sized so the whole
stack's aggregate risk stays inside the account's risk parameters.

  BUY shelves (discount → hunt longs):
    3840            primary daily order block (this week's next buy limit)
    3886 – 3941     weekly fib support shelf
    3328 – 3436     deep-discount macro accumulation (if the dollar rips)
  SELL shelves (premium → hunt shorts):
    4045 – 4065     +1SD pre-London sell-limit / supply
    4155 – 4211     higher weekly fib supply (swing target)

`zone_for(price)` names where price sits; `sniper_stack(...)` builds the layered
limit cards; `zone_budget(price)` reports how many pips to the next zone each way
(the CBDR range-to-range "how many pips can we bag" read).
"""

from __future__ import annotations

from typing import List, Optional

from gold.risk_engine import (GOLD_PIP, GOLD_USD_PER_POINT, size_for_risk,
                              _round_lot, MIN_LOT)
from gold.exposure import DEFAULT_RISK_CAP_USD

# Named OB zones from the drawn fib map. Each: (name, side, low, high, kind).
# Ordered low→high so neighbour lookups are simple.
ZONES = [
    {"name": "deep_discount_3328_3436", "side": "buy", "low": 3328.709, "high": 3435.654,
     "kind": "macro_accumulation", "note": "deep discount — load only if DXY reclaims its ceiling"},
    {"name": "ob_3840", "side": "buy", "low": 3820.0, "high": 3860.0,
     "kind": "daily_order_block", "note": "primary daily OB — the next buy limit"},
    {"name": "shelf_3886_3941", "side": "buy", "low": 3886.122, "high": 3941.260,
     "kind": "fib_support", "note": "weekly fib support shelf"},
    {"name": "sell_4045_4065", "side": "sell", "low": 4045.0, "high": 4065.0,
     "kind": "sd_supply", "note": "+1SD pre-London sell-limit / supply"},
    {"name": "supply_4155_4211", "side": "sell", "low": 4155.790, "high": 4211.260,
     "kind": "fib_supply", "note": "higher weekly fib supply — swing target"},
]

# Stop buffer beyond a zone edge, in gold price (protects the shared stack stop).
DEFAULT_ZONE_BUFFER = 8.0


def _pips(a: float, b: float) -> float:
    return round(abs(a - b) / GOLD_PIP, 1)


def zone_for(price: float) -> dict:
    """The zone price is inside (if any) plus the nearest buy zone below and sell
    zone above — the two rails of the current range."""
    inside = next((z for z in ZONES if z["low"] <= price <= z["high"]), None)
    below = [z for z in ZONES if z["high"] < price]
    above = [z for z in ZONES if z["low"] > price]
    nearest_below = max(below, key=lambda z: z["high"]) if below else None
    nearest_above = min(above, key=lambda z: z["low"]) if above else None
    return {"price": round(price, 2), "inside": inside,
            "nearest_below": nearest_below, "nearest_above": nearest_above}


def zone_budget(price: float) -> dict:
    """Zone-to-zone pip budget: pips down to the next buy shelf, pips up to the next
    sell shelf, and the round-trip 'bag' on the return leg."""
    z = zone_for(price)
    below, above = z["nearest_below"], z["nearest_above"]
    to_below = _pips(price, below["high"]) if below else None      # distance to buy shelf top
    to_above = _pips(price, above["low"]) if above else None       # distance to sell shelf base
    # The classic play: drop into the buy shelf, then bag the leg back to supply.
    bag = None
    if below and above:
        bag = _pips(below["high"], above["low"])
    return {
        "price": round(price, 2),
        "next_buy_zone": below and {"name": below["name"], "top": below["high"],
                                    "pips_away": to_below},
        "next_sell_zone": above and {"name": above["name"], "base": above["low"],
                                     "pips_away": to_above},
        "round_trip_bag_pips": bag,
        "note": (f"{to_below:.0f} pips to {below['name']}, "
                 f"{bag:.0f}-pip bag back to {above['name']}"
                 if (below and above) else "at a range extreme — no two-sided budget"),
    }


def sniper_stack(zone_name: str, balance: float, risk_usd: float = 20.0,
                 layers: int = 3, buffer: float = DEFAULT_ZONE_BUFFER,
                 target_price: Optional[float] = None) -> dict:
    """Build a layered limit stack for a named zone.

    The ``risk_usd`` is the budget for the WHOLE stack (not per layer) — it is split
    evenly across the layers and each layer is lot-sized to its own entry→stop
    distance, so the aggregate stack risk stays at ``risk_usd`` and inside the
    exposure cap. Entries ladder from the near edge into the zone (deeper = better
    price); the stop sits ``buffer`` beyond the far edge, shared by every layer.
    """
    z = next((x for x in ZONES if x["name"] == zone_name), None)
    if not z:
        return {"signal": "NO TRADE", "reason": f"unknown zone {zone_name}"}
    if layers < 1:
        return {"signal": "NO TRADE", "reason": "layers must be >= 1"}
    if risk_usd > DEFAULT_RISK_CAP_USD:
        return {"signal": "NO TRADE",
                "reason": f"stack risk ${risk_usd} exceeds exposure cap ${DEFAULT_RISK_CAP_USD}"}

    side = z["side"]
    low, high = z["low"], z["high"]
    if side == "buy":
        stop = round(low - buffer, 2)
        # ladder from top (first tap) down to low (best price)
        entries = [round(high - (high - low) * i / max(1, layers - 1), 2)
                   for i in range(layers)] if layers > 1 else [round((high + low) / 2, 2)]
    else:
        stop = round(high + buffer, 2)
        entries = [round(low + (high - low) * i / max(1, layers - 1), 2)
                   for i in range(layers)] if layers > 1 else [round((high + low) / 2, 2)]

    per_layer_risk = round(risk_usd / layers, 2)
    sig = "LONG" if side == "buy" else "SHORT"
    stack: List[dict] = []
    total_lot = 0.0
    clamped = False
    for i, entry in enumerate(entries, 1):
        want_lot = size_for_risk(entry, stop, per_layer_risk)
        lot = max(MIN_LOT, want_lot)
        if lot > want_lot:                      # min-lot forced us above the budget
            clamped = True
        dist = abs(entry - stop)
        r_actual = round(lot * dist * GOLD_USD_PER_POINT, 2)
        total_lot = _round_lot(total_lot + lot)
        card = {"layer": i, "side": side, "entry": entry, "stop": stop,
                "lot": lot, "risk_usd": r_actual,
                "stop_distance": round(dist, 2), "trade_type": "sniper",
                "label": f"{zone_name}_L{i}", "zone": zone_name,
                "reason": f"sniper layer {i}/{layers} into {zone_name} ({z['note']})"}
        if target_price is not None:
            card["targets"] = [round(target_price, 2)]
            card["rr"] = round(abs(target_price - entry) / dist, 2) if dist else None
        stack.append(card)

    stack_risk = round(sum(c["risk_usd"] for c in stack), 2)
    within_cap = stack_risk <= DEFAULT_RISK_CAP_USD
    return {
        "signal": sig, "zone": z, "layers": layers,
        "shared_stop": stop, "stack_risk_usd": stack_risk,
        "total_lot": total_lot, "per_layer_risk_usd": per_layer_risk,
        "avg_entry": round(sum(c["entry"] for c in stack) / len(stack), 2),
        "within_cap": within_cap, "min_lot_clamped": clamped,
        "orders": stack,
        "note": (f"{layers}-layer {sig} sniper stack into {zone_name} "
                 f"[{low}-{high}], shared stop {stop}, ~${stack_risk} total risk"
                 + ("" if within_cap else
                    f" — EXCEEDS ${DEFAULT_RISK_CAP_USD} cap (min-lot on a wide stop; "
                    "widen the account, drop layers, or tighten the zone)")),
    }
