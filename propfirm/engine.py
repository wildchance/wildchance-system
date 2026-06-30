"""Prop-firm risk gate + session grid — pure, deterministic, no I/O.

Encodes the TENDAJI risk cheat sheet (verified across the 1,250 / 2,500 / 5,000 /
10,000 USD tiers) as fractions of balance, then provides:

  • risk_limits(balance)        — every daily/weekly/monthly target & loss cap in USD
  • max_lot(balance)            — balance / 125,000  (0.02 @ 2,500)
  • evaluate_trade(...)         — the GATE: may a new trade open right now?
  • active_sessions(now)        — which CBDR session windows are open
  • grid_state(...)             — per-session on-log/off-log + remaining risk budget
  • lockin(closed, day_pnl, bal)— graduated target lock-in toward the 100-trade goal

The grid idea (the user's): each session window is a slot. Trades "on-log" when
their session is open and "off-log" when it closes, and total concurrent risk is
held under the daily loss cap so numerous assets across staggered sessions never
compound into oversized exposure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from cbdr.engine import WINDOWS, in_window

# Fractions of balance, straight from the cheat sheet (same for every tier).
LIMITS_PCT = {
    "daily_target_min": 0.003, "daily_target_max": 0.006,
    "daily_loss_soft": 0.0015, "daily_loss_cap": 0.003,
    "weekly_target_min": 0.015, "weekly_target_max": 0.030,
    "weekly_loss_soft": 0.0075, "weekly_loss_cap": 0.015,
    "monthly_target_min": 0.06, "monthly_target_max": 0.12,
    "monthly_loss_soft": 0.03, "monthly_loss_cap": 0.06,
}
LOT_DIVISOR = 125_000          # lot = balance / 125,000  (matches the sheet)
TRADE_TARGET = 100             # trades over which targets "lock in gradually"

# Target tiers. LIMITS_PCT above is the base "6" tier (6% monthly min target).
# Higher tiers scale the whole ladder — targets AND loss/drawdown caps rise
# together, because chasing 8/10/12% costs more risk. We default to the LEAST
# aggressive tier ("6") as accounts scale, and only step up on request. 10 is the
# common prop "Phase 1" profit target (e.g. FTMO/MFF 10%).
TARGET_TIERS = {
    "6":  {"scale": 1.0,      "monthly_target_min": 0.06, "label": "conservative (base start pack)"},
    "8":  {"scale": 8 / 6,    "monthly_target_min": 0.08, "label": "moderate"},
    "10": {"scale": 10 / 6,   "monthly_target_min": 0.10, "label": "prop phase-1 (10% target)"},
    "12": {"scale": 12 / 6,   "monthly_target_min": 0.12, "label": "aggressive"},
}
DEFAULT_TIER = "6"


def risk_limits(balance: float, tier: str = DEFAULT_TIER) -> Dict[str, float]:
    """All caps/targets in USD for a balance at a target tier (6 / 8 / 12).

    The base ladder is the cheat sheet's 6% tier; 8 and 12 scale every figure by
    the same factor so targets and drawdown move together. Unknown tier → base.
    """
    scale = TARGET_TIERS.get(str(tier), TARGET_TIERS[DEFAULT_TIER])["scale"]
    out = {k: round(balance * pct * scale, 4) for k, pct in LIMITS_PCT.items()}
    out["tier"] = str(tier) if str(tier) in TARGET_TIERS else DEFAULT_TIER
    out["tier_label"] = TARGET_TIERS[out["tier"]]["label"]
    out["max_drawdown_usd"] = out["monthly_loss_cap"]   # worst-case monthly cap
    return out


def max_lot(balance: float) -> float:
    return round(balance / LOT_DIVISOR, 2)


def evaluate_trade(balance: float, proposed_risk_usd: float,
                   day_pnl_usd: float = 0.0, open_risk_usd: float = 0.0,
                   tier: str = DEFAULT_TIER) -> dict:
    """The risk gate. Returns {allow, reason, ...} for a proposed new trade.

    Blocks when the daily loss cap is already hit, when adding the trade would
    breach the cap, or when the daily MAX target is reached (lock in the day).
    """
    lim = risk_limits(balance, tier)
    loss_cap = lim["daily_loss_cap"]
    target_max = lim["daily_target_max"]

    if day_pnl_usd <= -loss_cap:
        return {"allow": False, "reason": "daily max loss hit — stop trading today",
                "day_pnl": day_pnl_usd, "loss_cap": loss_cap}
    if day_pnl_usd >= target_max:
        return {"allow": False, "reason": "daily max target reached — lock in, stop",
                "day_pnl": day_pnl_usd, "target_max": target_max}

    projected_risk = open_risk_usd + proposed_risk_usd
    remaining = round(loss_cap - open_risk_usd, 4)
    if projected_risk > loss_cap + 1e-9:
        return {"allow": False,
                "reason": "would exceed daily risk budget",
                "open_risk": open_risk_usd, "proposed": proposed_risk_usd,
                "loss_cap": loss_cap, "remaining_budget": remaining}

    return {"allow": True, "reason": "within limits",
            "tier": lim["tier"],
            "remaining_budget": round(loss_cap - projected_risk, 4),
            "max_lot": max_lot(balance)}


def active_sessions(now: datetime, keys: Optional[List[str]] = None) -> List[str]:
    """Which CBDR session windows are open at `now` (an aware datetime).

    Each window declares its own tz, so DST is handled by converting `now` into
    the window's local clock before the in-window test.
    """
    out = []
    for key, w in WINDOWS.items():
        if keys is not None and key not in keys:
            continue
        local = now.astimezone(ZoneInfo(w.tz))
        minute = local.hour * 60 + local.minute
        if in_window(minute, w.start_min, w.end_min):
            out.append(key)
    return out


def grid_state(now: datetime, balance: float,
               open_trades: List[dict], tier: str = DEFAULT_TIER) -> dict:
    """Session grid: group open trades by session, report on-log/off-log + budget.

    open_trades: [{"id", "session", "risk_usd", "symbol"}]. A trade whose session
    is no longer active is flagged to OFF-LOG (manage/close); active ones stay
    ON-LOG. Remaining risk budget = daily loss cap minus total open risk.
    """
    lim = risk_limits(balance, tier)
    loss_cap = lim["daily_loss_cap"]
    active = active_sessions(now)

    total_open_risk = round(sum(float(t.get("risk_usd", 0)) for t in open_trades), 4)
    on_log, off_log = [], []
    by_session: Dict[str, dict] = {}
    for t in open_trades:
        sess = t.get("session")
        bucket = by_session.setdefault(sess, {"risk_usd": 0.0, "trades": 0, "active": sess in active})
        bucket["risk_usd"] = round(bucket["risk_usd"] + float(t.get("risk_usd", 0)), 4)
        bucket["trades"] += 1
        (on_log if sess in active else off_log).append(t.get("id", t.get("symbol")))

    remaining = round(loss_cap - total_open_risk, 4)
    return {
        "now_utc": now.astimezone(ZoneInfo("UTC")).isoformat(),
        "active_sessions": active,
        "daily_loss_cap": loss_cap,
        "open_risk_usd": total_open_risk,
        "remaining_budget_usd": remaining,
        "can_open_more": remaining > 0,
        "by_session": by_session,
        "on_log": on_log,        # trades in a still-active session
        "off_log": off_log,      # trades whose session closed — manage/close
    }


def lockin(closed_trades: int, day_pnl_usd: float, balance: float,
           tier: str = DEFAULT_TIER) -> dict:
    """Graduated lock-in toward the 100-trade goal.

    progress climbs to 1.0 over TRADE_TARGET trades. Once the daily MAX target is
    reached the risk scale drops to 0 (stop opening, lock the gains); otherwise it
    eases down as the sample matures so size is largest while the edge is unproven
    and tapers as targets bank.
    """
    lim = risk_limits(balance, tier)
    progress = min(1.0, max(0, closed_trades) / TRADE_TARGET)
    if day_pnl_usd >= lim["daily_target_max"]:
        scale, note = 0.0, "daily target banked — locked, stop opening"
    elif day_pnl_usd >= lim["daily_target_min"]:
        scale, note = round(1.0 - 0.5 * progress, 3), "min target met — easing size as sample matures"
    else:
        scale, note = round(1.0 - 0.25 * progress, 3), "building sample — near-full size"
    return {
        "closed_trades": closed_trades,
        "progress": round(progress, 3),
        "risk_scale": scale,         # multiply position size by this
        "note": note,
    }
