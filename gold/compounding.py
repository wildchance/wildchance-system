"""Gold compounding / growth ladders (pure) — from the handwritten plan.

Three deposit-growth models, each usable in KES / USD / KWD (same numbers, the
real value differs by currency):

  swing   700-base, lots ×10 (0.01→0.10→1.00→10.00), profit = 15,000 × lot
          → 700 → 850 → 2,350 → 17,350 → 167,350   (the Tuesday weekly swing)
  low     < 700, lots ×2 (0.01…5.12), profit = 5,500 × lot   (low-income build-up)
  mixed   700-base, lots ×2 but the profit RATE steps from low → swing at a lot
          threshold (the mid-tier "growing structure mixing both")

Profit rates are the per-0.01-lot targets you wrote: swing $150, low $55.
"""

from __future__ import annotations

from typing import List, Optional

RATE = {"swing": 15000.0, "low": 5500.0}      # profit = RATE × lot  → 0.01 lot: 150 / 55
CURRENCIES = ("USD", "KES", "KWD")

_SWING_LOTS = [0.01, 0.10, 1.00, 10.00]
_DOUBLING_LOTS = [round(0.01 * (2 ** i), 2) for i in range(12)]   # 0.01 … 20.48


def _profit(lot: float, rate: float) -> float:
    return round(rate * lot, 2)


def ladder(deposit: float, mode: str = "swing", currency: str = "USD",
           mix_switch_lot: float = 0.32) -> dict:
    """Compounding step table for a deposit under a growth ``mode``."""
    mode = mode.lower()
    cur = currency.upper()
    if mode == "swing":
        lots = _SWING_LOTS
    elif mode in ("low", "mixed"):
        lots = _DOUBLING_LOTS
    else:
        raise ValueError("mode must be swing | low | mixed")

    bal = float(deposit)
    steps: List[dict] = []
    for lot in lots:
        if mode == "low":
            rate = RATE["low"]
        elif mode == "swing":
            rate = RATE["swing"]
        else:  # mixed — low rate until the switch lot, then swing rate
            rate = RATE["low"] if lot < mix_switch_lot else RATE["swing"]
        p = _profit(lot, rate)
        bal += p
        steps.append({"lot": lot, "profit": p, "balance": round(bal, 2)})

    return {
        "mode": mode, "currency": cur, "deposit": deposit,
        "final_balance": round(bal, 2),
        "total_profit": round(bal - deposit, 2),
        "steps": steps,
    }


def target_to_lot(target_usd: float, mode: str = "swing") -> float:
    """Lot needed to hit a per-trade profit target at the mode's rate."""
    rate = RATE["swing" if mode == "swing" else "low"]
    return round(target_usd / rate, 2) if rate else 0.0


def plan(deposit: float, currency: str = "USD") -> dict:
    """Pick the model by deposit size and return the growth ladder + which tier."""
    tier = "low" if deposit < 700 else "swing"
    return {
        "deposit": deposit, "currency": currency.upper(), "tier": tier,
        "note": ("< 700 → low-income doubling build-up; "
                 "≥ 700 → Tuesday-swing ×10 ladder; use mode=mixed for the mid-tier"),
        "recommended": ladder(deposit, tier, currency),
        "swing": ladder(max(deposit, 700), "swing", currency),
        "low": ladder(deposit, "low", currency),
        "mixed": ladder(max(deposit, 700), "mixed", currency),
    }
