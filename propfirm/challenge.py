"""Prop-firm CHALLENGE rules (FundingPips / FTMO style) + equity kill-switch math.

Complements ``propfirm.engine`` (the TENDAJI per-session cheat-sheet gate). This
module answers the evaluation questions: what are the firm's hard lines, where do
we stop *before* them (buffers), how much can each trade risk, and how many losers
in a row can the account survive.

Pure + deterministic. The VPS connector (mt5_bridge/equity_guard.py) and the MQL5
EA implement the SAME guard so the app, the bridge and the EA agree.

FIRM RULES CHANGE — presets reflect FundingPips 2-Step Standard / FTMO as publicly
described in 2026; confirm on the firm's site and override via the query params.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

# Firm hard lines (fractions). daily_loss is measured from the HIGHER of
# start-of-day balance or equity (FundingPips) / start-of-day balance (FTMO).
PRESETS: Dict[str, Dict] = {
    "fundingpips_2step_8": {"label": "FundingPips 2-Step Standard (8% / 5%)",
                            "targets": [0.08, 0.05], "daily_loss": 0.05,
                            "max_loss": 0.10, "min_days": 3,
                            "daily_ref": "max(balance,equity) at day start"},
    "fundingpips_2step_10": {"label": "FundingPips 2-Step Standard (10% / 5%)",
                             "targets": [0.10, 0.05], "daily_loss": 0.05,
                             "max_loss": 0.10, "min_days": 3,
                             "daily_ref": "max(balance,equity) at day start"},
    "fundingpips_2step_pro": {"label": "FundingPips 2-Step Pro (6% / 6%)",
                              "targets": [0.06, 0.06], "daily_loss": 0.03,
                              "max_loss": 0.06, "min_days": 1,
                              "daily_ref": "max(balance,equity) at day start"},
    "ftmo_2step": {"label": "FTMO 2-Step (10% / 5%)", "targets": [0.10, 0.05],
                   "daily_loss": 0.05, "max_loss": 0.10, "min_days": 4,
                   "daily_ref": "balance at day start"},
}
DEFAULT_PRESET = "fundingpips_2step_8"

# Our own stop lines sit INSIDE the firm's (spread spikes / slippage / swaps can
# overshoot a stop). 70% of the daily limit and 80% of the max loss by default →
# 3.5% / 8% on a 5% / 10% account, matching the WildChance v4 spec.
DAILY_BUFFER = 0.70
MAX_BUFFER = 0.80


def guard_lines(initial_balance: float, day_start_balance: float,
                day_start_equity: float, preset: str = DEFAULT_PRESET,
                daily_buffer: float = DAILY_BUFFER, max_buffer: float = MAX_BUFFER) -> Dict:
    """Equity levels (account currency) for the firm's breach lines and ours."""
    p = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    ref = (max(day_start_balance, day_start_equity) if "max" in p["daily_ref"]
           else day_start_balance)
    return {
        "preset": preset if preset in PRESETS else DEFAULT_PRESET,
        "daily_reference": round(ref, 2),
        "firm_daily_floor": round(ref - initial_balance * p["daily_loss"], 2),
        "guard_daily_floor": round(ref - initial_balance * p["daily_loss"] * daily_buffer, 2),
        "firm_max_floor": round(initial_balance * (1 - p["max_loss"]), 2),
        "guard_max_floor": round(initial_balance * (1 - p["max_loss"] * max_buffer), 2),
    }


def equity_guard(equity: float, initial_balance: float, day_start_balance: float,
                 day_start_equity: float, phase: int = 1, preset: str = DEFAULT_PRESET,
                 daily_buffer: float = DAILY_BUFFER, max_buffer: float = MAX_BUFFER,
                 lock_on_target: bool = True) -> Dict:
    """THE kill-switch verdict for the current equity.

    action: TRADE | DAILY_LOCK (flatten, stop until next server day) |
            MAX_LOCK (flatten, stop for good) | TARGET_LOCK (flatten, phase passed).
    """
    p = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    lines = guard_lines(initial_balance, day_start_balance, day_start_equity,
                        preset, daily_buffer, max_buffer)
    targets = p["targets"]
    target = initial_balance * (1 + targets[min(max(phase, 1), len(targets)) - 1])
    out = dict(lines, equity=round(equity, 2), target_equity=round(target, 2))
    if equity <= lines["guard_max_floor"]:
        out.update(action="MAX_LOCK", reason="max-loss guard hit — flatten and stop")
    elif equity <= lines["guard_daily_floor"]:
        out.update(action="DAILY_LOCK", reason="daily-loss guard hit — flatten, resume next day")
    elif lock_on_target and equity >= target:
        out.update(action="TARGET_LOCK", reason="phase target reached — flatten and stop")
    else:
        out.update(action="TRADE", reason="within limits")
    out["daily_room"] = round(equity - lines["guard_daily_floor"], 2)
    out["max_room"] = round(equity - lines["guard_max_floor"], 2)
    return out


def challenge_plan(balance: float, preset: str = DEFAULT_PRESET,
                   risk_pct: float = 0.5, avg_r: float = 1.8,
                   win_rate: float = 0.45, max_concurrent: int = 3) -> Dict:
    """Risk-per-trade arithmetic for passing: losers-to-breach, trades-to-target."""
    p = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    risk = risk_pct / 100.0
    daily_guard = p["daily_loss"] * DAILY_BUFFER
    max_guard = p["max_loss"] * MAX_BUFFER
    exp_r = win_rate * avg_r - (1 - win_rate)              # expectancy in R
    phases = []
    for i, tgt in enumerate(p["targets"], start=1):
        need_r = tgt / risk if risk else math.inf
        trades = math.ceil(need_r / exp_r) if exp_r > 0 else None
        phases.append({"phase": i, "target_pct": tgt * 100,
                       "target_usd": round(balance * tgt, 2),
                       "net_r_needed": round(need_r, 1),
                       "expected_trades": trades})
    warnings = []
    if max_concurrent * risk > daily_guard:
        warnings.append(f"{max_concurrent} concurrent × {risk_pct}% = "
                        f"{max_concurrent * risk_pct:.2f}% can exceed the "
                        f"{daily_guard * 100:.2f}% daily guard in one bad hour")
    if exp_r <= 0:
        warnings.append("negative expectancy at these inputs — the challenge "
                        "cannot be passed on average; fix win-rate/R first")
    if risk_pct > 1.0:
        warnings.append("risk > 1%/trade: 4–5 straight losers end the day")
    return {
        "preset": p["label"], "balance": balance, "risk_pct": risk_pct,
        "risk_usd": round(balance * risk, 2),
        "losers_to_daily_guard": math.floor(daily_guard / risk + 1e-9) if risk else None,
        "losers_to_max_guard": math.floor(max_guard / risk + 1e-9) if risk else None,
        "expectancy_r": round(exp_r, 3), "phases": phases,
        "firm": {k: p[k] for k in ("daily_loss", "max_loss", "min_days", "daily_ref")},
        "guards_pct": {"daily": round(daily_guard * 100, 2), "max": round(max_guard * 100, 2)},
        "warnings": warnings,
    }


def size_lots(equity: float, risk_pct: float, sl_distance: float,
              value_per_unit_per_lot: float, lot_step: float = 0.01,
              min_lot: float = 0.01, max_lot: float = 100.0) -> Optional[float]:
    """Lots so that hitting SL loses ``risk_pct`` of equity. Returns None (SKIP) when
    even the minimum lot would over-risk — never round UP into extra risk."""
    if sl_distance <= 0 or value_per_unit_per_lot <= 0 or equity <= 0:
        return None
    raw = equity * risk_pct / 100.0 / (sl_distance * value_per_unit_per_lot)
    lots = math.floor(raw / lot_step + 1e-9) * lot_step
    if lots < min_lot:
        return None
    return round(min(lots, max_lot), 2)
