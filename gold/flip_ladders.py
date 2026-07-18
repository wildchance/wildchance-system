"""Account-tier flip ladders — the deposit→target growth engine (pure).

Encodes the handwritten three-tier growth plan (2026-07-18 notes) so every account
size maps to a run cadence, a per-run pip target, and a projected balance curve.
The SAME gold system (500-pip / 1500-pip runs) drives all three; only the sizing
cadence and denomination change.

  TIER 1 — Cent flipper (deposit < 700):
      lot-doubling ladder 0.01 → 0.02 → … → 5.12, one 500-pip run per step,
      ~10 runs. Reproduces the sheet exactly: 700 → 56,965 (81.4×).
  TIER 2 — Middle income (deposit 700 – 5,000; 5,000 = drawdown benchmark):
      12 runs in cycles of [500, 500, 1500] pips — after two 500-pip runs, hunt a
      1,500-pip run — repeating until the account grows toward ~500,000.
  TIER 3 — Flipper (deposit ≥ 5,000):
      1,500-pip runs only, same denominations.

Denominations: cent, USD, KES, KWD (same structure, different unit). 5,000 is the
benchmark deposit because of drawdown risk. `plan(deposit, denom)` picks the tier
and returns its ladder + projection.
"""

from __future__ import annotations

from typing import List, Optional

# Per-run value of ONE 500-pip run at the minimum (0.01) lot, per the sheet's
# doubling column (55 → 110 → 220 → …). Calibration constant of the cent contract.
CENT_RUN_AT_MIN_LOT = 55.0

# Deposit thresholds (in the account's own unit) that pick the tier.
TIER_BOUNDS = {"cent_max": 700.0, "middle_max": 5000.0}

# Denomination table — informational: typical deposit ranges from the notes.
DENOMINATIONS = {
    "cent": {"unit": "cents", "typical_deposit": [700, 1000], "benchmark": 5000},
    "USD":  {"unit": "USD", "typical_deposit": [700, 5000], "benchmark": 5000},
    "KES":  {"unit": "KES", "typical_deposit": [700, 5000], "benchmark": 5000},
    "KWD":  {"unit": "KWD", "typical_deposit": [700, 5000], "benchmark": 5000},
}

# Run cadences (pip target per run).
CENT_PIPS = 500
MIDDLE_CYCLE = (500, 500, 1500)      # 2× 500-pip then 1× 1500-pip
FLIPPER_PIPS = 1500


def account_tier(deposit: float) -> str:
    """Classify a deposit into cent_flipper / middle / flipper."""
    if deposit < TIER_BOUNDS["cent_max"]:
        return "cent_flipper"
    if deposit <= TIER_BOUNDS["middle_max"]:
        return "middle"
    return "flipper"


def cent_flipper(deposit: float = 700.0, runs: int = 10,
                 run_at_min_lot: float = CENT_RUN_AT_MIN_LOT,
                 denom: str = "cent") -> dict:
    """The lot-doubling cent ladder: one 500-pip run per step, lot doubles each run.

    Row i value = run_at_min_lot × 2^(i-1); balance compounds. With the defaults
    this reproduces the sheet: 700 → 56,965 over 10 runs.
    """
    rows: List[dict] = []
    balance = float(deposit)
    lot = 0.01
    for i in range(1, runs + 1):
        gain = round(run_at_min_lot * (2 ** (i - 1)), 2)
        balance = round(balance + gain, 2)
        rows.append({"run": i, "lot": round(lot, 2), "pips": CENT_PIPS,
                     "gain": gain, "balance": balance})
        lot *= 2
    return {
        "tier": "cent_flipper", "denom": denom, "deposit": deposit,
        "runs": runs, "pip_target": CENT_PIPS, "final_balance": balance,
        "multiple": round(balance / deposit, 2) if deposit else None,
        "ladder": rows,
        "note": (f"{runs} × 500-pip lot-doubling runs: {denom} {deposit:g} → "
                 f"{balance:g} ({round(balance / deposit, 1)}×)" if deposit else ""),
    }


def middle_ladder(deposit: float = 5000.0, cycles: int = 4,
                  denom: str = "USD") -> dict:
    """The 12-run middle ladder in [500, 500, 1500]-pip cycles.

    Reports the run cadence + cumulative pip campaign (2,500 pips/cycle) toward the
    ~500,000 objective; sizing per run is the account's phase-ladder lot (see
    risk_engine.phase_plan / lot_ladder), kept within drawdown parameters.
    """
    rows: List[dict] = []
    run_no = 0
    cum_pips = 0
    for c in range(1, cycles + 1):
        for pip in MIDDLE_CYCLE:
            run_no += 1
            cum_pips += pip
            rows.append({"run": run_no, "cycle": c, "pips": pip,
                         "kind": "anchor" if pip == 500 else "extension",
                         "cumulative_pips": cum_pips})
    return {
        "tier": "middle", "denom": denom, "deposit": deposit,
        "benchmark": DENOMINATIONS.get(denom, {}).get("benchmark", 5000),
        "runs": run_no, "cycles": cycles, "cycle_pips": sum(MIDDLE_CYCLE),
        "cumulative_pips": cum_pips, "objective": "grow toward ~500,000",
        "cadence": list(MIDDLE_CYCLE), "ladder": rows,
        "note": (f"{run_no} runs in {cycles} × [500,500,1500] cycles = "
                 f"{cum_pips:,} pips; 5,000 is the drawdown benchmark"),
    }


def flipper(deposit: float = 5000.0, runs: int = 6, denom: str = "USD") -> dict:
    """The pure flipper: 1,500-pip runs only, same denominations."""
    rows = [{"run": i, "pips": FLIPPER_PIPS, "cumulative_pips": FLIPPER_PIPS * i}
            for i in range(1, runs + 1)]
    return {
        "tier": "flipper", "denom": denom, "deposit": deposit,
        "runs": runs, "pip_target": FLIPPER_PIPS,
        "cumulative_pips": FLIPPER_PIPS * runs, "ladder": rows,
        "note": f"{runs} × 1,500-pip runs = {FLIPPER_PIPS * runs:,} pips ({denom})",
    }


def plan(deposit: float, denom: str = "USD", runs: Optional[int] = None) -> dict:
    """Pick the tier for a deposit and return its ladder + projection."""
    tier = account_tier(deposit)
    if tier == "cent_flipper":
        return cent_flipper(deposit, runs or 10, denom=denom)
    if tier == "middle":
        return middle_ladder(deposit, cycles=(runs or 12) // 3, denom=denom)
    return flipper(deposit, runs or 6, denom=denom)
