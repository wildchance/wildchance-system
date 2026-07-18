"""US Dollar Index (DXY) regime + the dollar→gold inverse gate (pure, stdlib-only).

Encodes the weekly DXY anticipation structure (TradingView, 2026-07-06) for the
Trump-term cycle and turns it into a gold bias, because gold trades inverse to the
real broad dollar: dollar DOWN → gold BMS (bullish); dollar UP → gold SMS
(bearish). This is the structural confluence behind the "buy gold dips" thesis —
the dollar is expected to WEAKEN through most of the term before a late rally.

Anticipated path (weekly closes):
  • now ~100.8, below the 102.50 (1.0 fib) pivot — dollar heavy;
  • near-term BOUNCE risk into 105.17 (0.5) / 107.49 (0.618) — the sell-the-dollar
    band and, for gold, a temporary headwind / the dip to buy;
  • primary DECLINE through 2027–2028 toward the 92.99 / 87.71 demand shelf
    (−0.5 / −0.618 extension) = the structural gold tailwind;
  • LATE-CYCLE rally into 2029–2030 back toward 114–118 = the eventual gold
    headwind / distribution window.

Levels are the drawn fib map; classification is deterministic from a DXY price.
"""

from __future__ import annotations

from typing import Optional

# Fib / structure levels off the drawn weekly map.
PIVOT = 102.50              # 1.0 — reclaim = dollar bid returns
SELL_BAND = (105.172, 107.494)   # 0.5 / 0.618 — anticipated bounce = sell dollar
TOP = 114.539              # 0 — cycle high / late-term target zone (→118)
DEMAND_SHELF = (87.713, 92.987)  # −0.618 / −0.5 — 2028-ish bottom = peak gold tailwind
CURRENT = 100.810          # 2026-07-06 weekly close

# Standing anticipation while the primary decline is intact (Trump term).
ANTICIPATED = {
    "as_of": "2026-07-06",
    "term_bias": "bearish",          # dollar weakens through most of the term
    "gold_implication": "bullish",   # inverse → structural gold tailwind
    "near_term_risk": "bounce into 105.17–107.49 before the decline resumes",
    "structural_target": DEMAND_SHELF,
    "late_cycle": "rally toward 114–118 into 2029–2030 (eventual gold headwind)",
}


def dollar_regime(price: Optional[float] = None) -> dict:
    """Classify the DXY regime (BMS up / SMS down / range) from a weekly price.

    With no price, returns the standing anticipation (primary decline intact).
    """
    if price is None:
        return {"price": CURRENT, "regime": "sms", "phase": "primary_decline",
                "note": "no live DXY — using anticipated primary decline (dollar bearish)",
                **ANTICIPATED}
    if price >= TOP:
        regime, phase, note = "bms", "cycle_high", "at/above the 114.5 cycle high — late-term dollar peak"
    elif price >= SELL_BAND[0]:
        regime, phase, note = "range", "bounce", "in the 105–107 sell-the-dollar band — fade rallies"
    elif price >= PIVOT:
        regime, phase, note = "range", "pivot", "back above 102.50 pivot — dollar bid returning, watch gold"
    elif price > DEMAND_SHELF[1]:
        regime, phase, note = "sms", "primary_decline", "below the 102.50 pivot — dollar in decline (gold tailwind)"
    else:
        regime, phase, note = "sms", "demand_shelf", "into 88–93 demand shelf — max gold tailwind / dollar bottoming"
    return {"price": price, "regime": regime, "phase": phase, "note": note,
            "term_bias": ANTICIPATED["term_bias"]}


def gold_from_dollar(price: Optional[float] = None) -> dict:
    """The gold bias implied by the dollar regime (strict inverse)."""
    reg = dollar_regime(price)
    if reg["regime"] == "bms":
        bias, note = "short", "dollar BMS (strong) — gold SMS bias, fade gold strength"
    elif reg["regime"] == "sms":
        bias, note = "long", "dollar SMS (weak) — gold BMS bias, buy gold dips"
    else:
        # In the bounce/pivot range the dollar headwind is temporary → the gold DIP.
        bias, note = "long", "dollar bouncing into resistance — temporary gold headwind = the dip to buy"
    return {"gold_bias": bias, "dollar_regime": reg["regime"], "dollar_phase": reg["phase"],
            "note": note, "dxy": reg["price"]}


def confluence(side: str, price: Optional[float] = None) -> str:
    """confirms / diverges / neutral for a gold side vs the dollar-implied bias."""
    want = "long" if side.lower() in ("long", "buy") else "short"
    gb = gold_from_dollar(price)["gold_bias"]
    return "confirms" if gb == want else "diverges"


# --- monthly fib structure triggers (1M chart, 2026-07-18) -------------------
# The monthly DXY fib book gives two structural bands that read GOLD directly
# (inverse). A DXY print inside the LAST_DISCOUNT band = the dollar bottoming =
# gold's final discount to load; inside the CEILING band = the dollar reclaiming
# value = gold's ceiling confirmed / distribute.
GOLD_TRIGGERS = {
    "last_discount": (93.308, 96.265),   # DXY here → gold deep-buy (dollar bottoming)
    "ceiling":       (104.589, 107.103), # DXY here → gold distribution (dollar reclaims)
}


def gold_structure_trigger(price: Optional[float] = None) -> dict:
    """Map a DXY price to a GOLD structural trigger off the monthly fib bands.

    Returns the trigger name, the implied gold bias/strength, and whether we are
    in a band. With no price, falls back to the anticipated structure (no trigger).
    """
    if price is None:
        return {"trigger": None, "gold_bias": ANTICIPATED["gold_implication"][:4] == "bull"
                and "long" or "short", "strength": "structural", "dxy": CURRENT,
                "note": "no live DXY — anticipated structure, no band trigger"}
    lo_d, hi_d = GOLD_TRIGGERS["last_discount"]
    lo_c, hi_c = GOLD_TRIGGERS["ceiling"]
    if price <= hi_d:
        return {"trigger": "gold_last_discount", "gold_bias": "long", "strength": "max",
                "band": GOLD_TRIGGERS["last_discount"], "dxy": price,
                "note": f"DXY {price} at/under {hi_d} — dollar bottoming, load gold's last discount"}
    if lo_c <= price <= hi_c:
        return {"trigger": "gold_ceiling", "gold_bias": "short", "strength": "cap",
                "band": GOLD_TRIGGERS["ceiling"], "dxy": price,
                "note": f"DXY {price} in {lo_c}-{hi_c} ceiling — gold cap confirmed, distribute"}
    if price > hi_c:
        return {"trigger": "dollar_premium", "gold_bias": "short", "strength": "structural",
                "dxy": price, "note": f"DXY {price} above the {hi_c} ceiling — sustained gold headwind"}
    return {"trigger": None, "gold_bias": "long", "strength": "neutral", "dxy": price,
            "note": f"DXY {price} between the discount and ceiling bands — no structural trigger"}
