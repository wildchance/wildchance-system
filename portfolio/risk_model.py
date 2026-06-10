"""Dynamic Risk Management Model — risk budget, exposure caps, drawdown control.

Three layers, all transcribed from the framework:

  1. Per-trade risk by tier:   Tier 1 = 1.5%, Tier 2 = 1.0%, Tier 3 = 0.5%.
  2. Maximum exposure by asset basket (% of the 100% portfolio risk budget):
        JPY 25, USD 25, EUR 20, Metals 15, EM FX 15.
  3. Drawdown controls — escalating de-risking as the account bleeds:
        5% -> cut risk 25%, 10% -> cut 50%, 15% -> close weakest, 20% -> reset.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

# --- Per-trade risk by tier (fraction of capital) ---------------------------
PER_TRADE_RISK = {
    1: 0.015,   # 1.5%
    2: 0.010,   # 1.0%
    3: 0.005,   # 0.5%
}

# --- Maximum exposure per asset basket (fraction of the risk budget) --------
MAX_EXPOSURE = {
    "JPY": 0.25,
    "USD": 0.25,
    "EUR": 0.20,
    "METALS": 0.15,
    "EM_FX": 0.15,
}

# --- Drawdown controls: peak-to-trough drawdown -> action -------------------
# ``risk_multiplier`` is the fraction of normal risk to run after the action.
DRAWDOWN_CONTROLS = [
    {"drawdown": 0.05, "action": "Reduce risk 25%", "risk_multiplier": 0.75},
    {"drawdown": 0.10, "action": "Reduce risk 50%", "risk_multiplier": 0.50},
    {"drawdown": 0.15, "action": "Close weakest positions", "risk_multiplier": 0.50},
    {"drawdown": 0.20, "action": "Portfolio reset", "risk_multiplier": 0.0},
]


def per_trade_risk_pct(tier: int) -> float:
    """Risk fraction for a tier. Tier 4 (metals) is managed as a hedge book,
    not on the per-trade ladder, so it is not in the table."""
    if tier not in PER_TRADE_RISK:
        raise ValueError(f"no per-trade risk defined for tier {tier!r}")
    return PER_TRADE_RISK[tier]


def position_risk(balance: float, tier: int) -> float:
    """Dollar risk for one position of the given tier."""
    return round(balance * per_trade_risk_pct(tier), 2)


def _norm_group(group: str) -> str:
    g = group.strip().upper().replace(" ", "_")
    aliases = {"EMFX": "EM_FX", "EM": "EM_FX", "GOLD": "METALS", "METAL": "METALS"}
    g = aliases.get(g, g)
    if g not in MAX_EXPOSURE:
        raise ValueError(f"unknown asset group {group!r}; known: {sorted(MAX_EXPOSURE)}")
    return g


def check_group_exposure(group: str, current_exposure_pct: float) -> dict:
    """Is the current exposure to a basket within its cap?"""
    g = _norm_group(group)
    cap = MAX_EXPOSURE[g]
    return {
        "group": g,
        "current": round(current_exposure_pct, 4),
        "cap": cap,
        "within_cap": current_exposure_pct <= cap,
        "headroom": round(cap - current_exposure_pct, 4),
    }


def aggregate_exposure(exposures: Dict[str, float]) -> dict:
    """Check a whole map of {group: exposure_pct} against every cap at once."""
    checks = [check_group_exposure(g, pct) for g, pct in exposures.items()]
    return {
        "checks": checks,
        "all_within_caps": all(c["within_cap"] for c in checks),
        "breaches": [c["group"] for c in checks if not c["within_cap"]],
    }


def drawdown_action(drawdown_pct: float) -> dict:
    """The control that fires at a given peak-to-trough drawdown.

    ``drawdown_pct`` is a positive fraction (0.07 == 7% down). Returns the
    deepest control whose trigger has been breached, or a normal-operation
    response below the first trigger.
    """
    if drawdown_pct < 0:
        raise ValueError("drawdown_pct must be non-negative")
    fired: Optional[dict] = None
    for control in DRAWDOWN_CONTROLS:
        if drawdown_pct >= control["drawdown"]:
            fired = control
    if fired is None:
        return {
            "drawdown": round(drawdown_pct, 4),
            "action": "Normal operation",
            "risk_multiplier": 1.0,
            "triggered": False,
        }
    return {
        "drawdown": round(drawdown_pct, 4),
        "action": fired["action"],
        "risk_multiplier": fired["risk_multiplier"],
        "triggered": True,
        "trigger_level": fired["drawdown"],
    }


@dataclass
class RiskBudget:
    balance: float
    drawdown_pct: float
    risk_multiplier: float
    per_trade: Dict[int, float]      # tier -> dollar risk after de-risking
    max_exposure: Dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


def risk_budget(balance: float, drawdown_pct: float = 0.0) -> RiskBudget:
    """The live risk budget: per-trade dollar risk per tier, scaled down by any
    active drawdown control, plus the static exposure caps."""
    dd = drawdown_action(drawdown_pct)
    mult = dd["risk_multiplier"]
    per_trade = {
        t: round(balance * pct * mult, 2) for t, pct in PER_TRADE_RISK.items()
    }
    return RiskBudget(
        balance=balance,
        drawdown_pct=round(drawdown_pct, 4),
        risk_multiplier=mult,
        per_trade=per_trade,
        max_exposure=dict(MAX_EXPOSURE),
    )
