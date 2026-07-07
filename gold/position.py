"""Swing-position lifecycle for gold trades (pure, stdlib-only).

A fired gold signal is no longer fire-and-forget: it becomes a tracked swing that
is HELD until a target is hit, break-even is trailed after TP1, and — per the
"till TP is hit by Monday close / Tuesday open" rule — is force-closed at a
weekly time-stop if the target has not printed by then.

This module is the deterministic decision core (no DB, no clock of its own): feed
it a position's state plus the current price and time, and it returns the next
action. `services.gold_positions` wraps it with persistence.

result_r is expressed in R (multiples of the initial entry→stop risk): a clean
stop is −1R, the 1:3 target is +3R, a break-even scratch is ~0R.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

# Monday time-stop, UTC. 22:00 UTC ≈ Monday's US close / the Tuesday Asia open —
# the "Monday close or Tuesday open" horizon for the weekend swing.
TIME_STOP_HOUR_UTC = 22
TIME_STOP_WEEKDAY = 0            # Monday


def swing_deadline(opened_at: dt.datetime) -> dt.datetime:
    """The Monday-close/Tuesday-open horizon on or after ``opened_at``.

    A trade opened over the weekend (Sunday open) or early week is held to the
    coming Monday 22:00 UTC; one already past that instant rolls to next Monday.
    """
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=dt.timezone.utc)
    days_ahead = (TIME_STOP_WEEKDAY - opened_at.weekday()) % 7
    candidate = opened_at.replace(hour=TIME_STOP_HOUR_UTC, minute=0, second=0,
                                  microsecond=0) + dt.timedelta(days=days_ahead)
    if candidate <= opened_at:
        candidate += dt.timedelta(days=7)
    return candidate


def _r(entry: float, exit_price: float, risk: float, long: bool) -> float:
    if risk <= 0:
        return 0.0
    return round(((exit_price - entry) if long else (entry - exit_price)) / risk, 4)


def evaluate(state: dict, price: float, now: dt.datetime) -> dict:
    """Decide the next action for an OPEN position at ``price`` / ``now``.

    ``state`` = {side, entry, stop (initial), targets:[tp1,tp2,tp3],
                 be_active, opened_at}. Returns the updated stop / be_active, the
    highest TP reached, and — when the trade should close — exit price/reason and
    realized R.
    """
    side = state["side"].lower()
    long = side in ("long", "buy")
    entry = float(state["entry"])
    stop0 = float(state["stop"])
    targets: List[float] = [float(t) for t in (state.get("targets") or [])]
    be_active = bool(state.get("be_active"))
    risk = abs(entry - stop0)

    # Highest target reached at this price.
    tp_hit = 0
    for i, tp in enumerate(targets, start=1):
        reached = price >= tp if long else price <= tp
        if reached:
            tp_hit = i

    # Break-even trail: once TP1 prints, the stop moves to entry and stays there.
    if tp_hit >= 1:
        be_active = True
    eff_stop = entry if be_active else stop0

    out = {"close": False, "exit_price": None, "exit_reason": None,
           "result_r": None, "be_active": be_active, "stop": eff_stop,
           "tp_hit": tp_hit, "note": ""}

    # Final target hit → close the runner.
    if targets and tp_hit >= len(targets):
        out.update(close=True, exit_price=targets[-1], exit_reason=f"TP{tp_hit}",
                   result_r=_r(entry, targets[-1], risk, long),
                   note=f"final target TP{tp_hit} hit")
        return out

    # Stop / break-even hit (approximated on the polled price).
    stopped = price <= eff_stop if long else price >= eff_stop
    if stopped:
        reason = "BE" if be_active and eff_stop == entry else "SL"
        out.update(close=True, exit_price=eff_stop, exit_reason=reason,
                   result_r=_r(entry, eff_stop, risk, long),
                   note=("trailed to break-even" if reason == "BE" else "initial stop hit"))
        return out

    # Weekly time-stop: not at target by Monday close / Tuesday open → close out.
    opened_at = state.get("opened_at")
    if opened_at is not None and now >= swing_deadline(opened_at):
        out.update(close=True, exit_price=price, exit_reason="TIME",
                   result_r=_r(entry, price, risk, long),
                   note="weekly time-stop (Monday close / Tuesday open)")
        return out

    out["note"] = (f"holding — TP{tp_hit} reached" if tp_hit else "holding — no target yet")
    return out
