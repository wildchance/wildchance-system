"""The Big-5 pip-capture tiers — name every gold move by how many pips it banks.

A captured (or targeted) move is graded like the Big Five, fastest → heaviest:

    🐆 cheetah   250 pips     🐆 leopard  500 pips     🦁 lion      750 pips
    🐃 buffalo  1000 pips     🦏 rhino   1250 pips     🐘 elephant 1500 pips

Cheetah (250 pips) is the MINIMUM capture the system holds for — a hold is not
released for less. Within one trend you can bank several shorter targets (the day's
levels), and the tier names the size of each. Pips use the gold convention
(1 pip = $0.10 move), so cheetah = $25 / elephant = $150 per 0.01 lot ladder step.
"""

from __future__ import annotations

from typing import Optional

from gold.risk_engine import GOLD_PIP

# (min pips, name, emoji) ordered light → heavy.
BIG5 = [
    (250, "cheetah", "🐆"),
    (500, "leopard", "🐆"),
    (750, "lion", "🦁"),
    (1000, "buffalo", "🐃"),
    (1250, "rhino", "🦏"),
    (1500, "elephant", "🐘"),
]

MIN_CAPTURE_PIPS = 250          # cheetah — the hold's minimum-capture floor


def pips_of(entry: float, exit_price: float) -> float:
    """Absolute pip distance between two prices (gold convention)."""
    return round(abs(exit_price - entry) / GOLD_PIP, 1)


def tier_for_pips(pips: float) -> Optional[dict]:
    """The highest Big-5 tier a pip count reaches (None if below cheetah)."""
    hit = None
    for min_pips, name, emoji in BIG5:
        if pips >= min_pips:
            hit = {"pips_floor": min_pips, "name": name, "emoji": emoji}
    return hit


def next_tier(pips: float) -> Optional[dict]:
    """The next tier up from the current pip count (None past elephant)."""
    for min_pips, name, emoji in BIG5:
        if pips < min_pips:
            return {"pips_floor": min_pips, "name": name, "emoji": emoji,
                    "pips_away": round(min_pips - pips, 1)}
    return None


def classify_capture(entry: float, exit_price: float, side: str) -> dict:
    """Grade a move entry→exit: pips, $ move, the Big-5 tier reached, next tier."""
    long = side.lower() in ("long", "buy")
    directional = (exit_price - entry) if long else (entry - exit_price)
    pips = round(directional / GOLD_PIP, 1)          # signed (negative = adverse)
    tier = tier_for_pips(pips) if pips > 0 else None
    return {
        "pips": pips, "usd_move": round(abs(directional), 2),
        "tier": tier, "next": next_tier(pips) if pips > 0 else next_tier(0),
        "meets_min": pips >= MIN_CAPTURE_PIPS,
        "label": (f"{tier['emoji']} {tier['name']} (+{int(pips)} pips)"
                  if tier else f"{int(pips)} pips (below cheetah)"),
    }
