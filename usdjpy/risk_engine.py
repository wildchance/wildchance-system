"""TENDAJI risk engine — position sizing & profit/loss targets per account size.

Derived from the TENDAJI RISK CHEAT SHEET. Every number in that sheet reduces
to clean rules of the account balance, verified against all ten columns
(1,250 -> 800,000 USD):

    lot per trade        = balance / 125,000     (0.01 lot per $1,250)

    daily   min profit   = 0.30% of balance   | its max loss = 0.15%
    daily   max profit   = 0.60% of balance   | its max loss = 0.30%
    weekly  min profit   = 1.50% of balance   | its max loss = 0.75%
    weekly  max profit   = 3.00% of balance   | its max loss = 1.50%
    monthly min profit   = 6.00% of balance   | its max loss = 3.00%
    monthly max profit   = 12.0% of balance   | its max loss = 6.00%

These are the "take-profit targets in various account sizes". For the USD/JPY
mean-reversion strategy the exit itself is time-based (day+3 close), so these
money figures are the targets/limits to manage the account TO, not per-trade
TP prices.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .engine import PIP

# The ten reference account sizes from the cheat sheet (USD).
ACCOUNT_SIZES: List[float] = [
    1_250, 2_500, 5_000, 10_000, 25_000,
    50_000, 100_000, 200_000, 400_000, 800_000,
]

# balance / LOT_DIVISOR = lot size (0.01 per $1,250).
LOT_DIVISOR = 125_000

# (profit %, max-loss %) of balance for each horizon/intensity.
TARGET_RULES = {
    "daily_min":   (0.0030, 0.0015),
    "daily_max":   (0.0060, 0.0030),
    "weekly_min":  (0.0150, 0.0075),
    "weekly_max":  (0.0300, 0.0150),
    "monthly_min": (0.0600, 0.0300),
    "monthly_max": (0.1200, 0.0600),
}


def lot_for_account(balance: float) -> float:
    """Fixed per-trade lot from the cheat sheet (rounded to 2 dp / 0.01 lots)."""
    return round(balance / LOT_DIVISOR, 2)


def pip_value_per_lot(usdjpy_price: float) -> float:
    """USD value of one USD/JPY pip per 1.0 standard lot (100,000 units).

    1 pip = 0.01 JPY on 100,000 units = 1,000 JPY = 1,000 / price USD.
    At ~150.00 that is about $6.67 per pip per standard lot.
    """
    return 1_000.0 / usdjpy_price


@dataclass
class RiskProfile:
    account_size: float
    lot_per_trade: float
    targets: Dict[str, Dict[str, float]]  # horizon -> {profit, max_loss}

    def to_dict(self) -> dict:
        return asdict(self)


def risk_profile(balance: float) -> RiskProfile:
    """Full sizing + target/limit profile for an arbitrary account balance."""
    targets: Dict[str, Dict[str, float]] = {}
    for name, (profit_pct, loss_pct) in TARGET_RULES.items():
        targets[name] = {
            # 4 dp keeps cheat-sheet values like 1.875 exact rather than
            # rounding them to 1.88.
            "profit": round(balance * profit_pct, 4),
            "max_loss": round(balance * loss_pct, 4),
            "profit_pct": profit_pct,
            "max_loss_pct": loss_pct,
        }
    return RiskProfile(
        account_size=balance,
        lot_per_trade=lot_for_account(balance),
        targets=targets,
    )


def risk_table() -> List[dict]:
    """Profiles for all ten reference account sizes (the cheat-sheet grid)."""
    return [risk_profile(size).to_dict() for size in ACCOUNT_SIZES]


def trade_money_risk(
    balance: float,
    stop_pips: float,
    usdjpy_price: float,
    lot: Optional[float] = None,
) -> dict:
    """Estimate the money at risk for a specific USD/JPY trade.

    The cheat-sheet lot is FIXED per account, while this strategy's stop
    distance (0.5*SD20) varies with volatility. This function converts that
    stop distance into an estimated dollar loss so you can confirm the trade
    fits inside your daily/weekly max-loss limits, and flags it if it does not.
    """
    if lot is None:
        lot = lot_for_account(balance)

    risk_usd = lot * pip_value_per_lot(usdjpy_price) * stop_pips

    profile = risk_profile(balance)
    daily_cap = profile.targets["daily_max"]["max_loss"]
    weekly_cap = profile.targets["weekly_max"]["max_loss"]

    return {
        "account_size": balance,
        "lot": lot,
        "usdjpy_price": usdjpy_price,
        "stop_pips": round(stop_pips, 1),
        "pip_value_per_lot": round(pip_value_per_lot(usdjpy_price), 4),
        "estimated_risk_usd": round(risk_usd, 2),
        "daily_max_loss_cap": daily_cap,
        "weekly_max_loss_cap": weekly_cap,
        "within_daily_cap": risk_usd <= daily_cap,
        "within_weekly_cap": risk_usd <= weekly_cap,
    }
