"""USD/JPY daily mean-reversion forward-test subsystem.

This package automates the manual "paste the daily close into the tracker"
workflow described in USDJPY_FORWARD_TEST.xlsx. The rules are FROZEN — see
``engine.FROZEN_RULES``. Do not change thresholds, hold period, or instrument
mid-test; doing so invalidates the forward test.
"""

from .engine import (
    FROZEN_RULES,
    Signal,
    evaluate_close,
    rolling_stats,
    result_r,
    Scoreboard,
    build_scoreboard,
)
from .risk_engine import (
    ACCOUNT_SIZES,
    risk_profile,
    risk_table,
    trade_money_risk,
)

__all__ = [
    "FROZEN_RULES",
    "Signal",
    "evaluate_close",
    "rolling_stats",
    "result_r",
    "Scoreboard",
    "build_scoreboard",
    "ACCOUNT_SIZES",
    "risk_profile",
    "risk_table",
    "trade_money_risk",
]
