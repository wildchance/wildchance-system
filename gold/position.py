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


def _aware(t: dt.datetime) -> dt.datetime:
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t


def swing_deadline(opened_at: dt.datetime) -> dt.datetime:
    """The Monday-close/Tuesday-open horizon on or after ``opened_at``.

    A trade opened over the weekend (Sunday open) or early week is held to the
    coming Monday 22:00 UTC; one already past that instant rolls to next Monday.
    """
    opened_at = _aware(opened_at)
    days_ahead = (TIME_STOP_WEEKDAY - opened_at.weekday()) % 7
    candidate = opened_at.replace(hour=TIME_STOP_HOUR_UTC, minute=0, second=0,
                                  microsecond=0) + dt.timedelta(days=days_ahead)
    if candidate <= opened_at:
        candidate += dt.timedelta(days=7)
    return candidate


def intraday_deadline(opened_at: dt.datetime, close_hour_utc: int = 21) -> dt.datetime:
    """Same-day horizon for an intraday trade — the NY close (21:00 UTC by default).

    If the trade opened after the close hour, it rolls to the next day's close.
    """
    opened_at = _aware(opened_at)
    d = opened_at.replace(hour=close_hour_utc, minute=0, second=0, microsecond=0)
    return d if d > opened_at else d + dt.timedelta(days=1)


def session_deadline(opened_at: dt.datetime, end_hour_utc: int) -> dt.datetime:
    """Session-end horizon for an intrasession trade — the QT session's end hour.

    ``end_hour_utc`` is the session boundary (Q1→6, Q2→12, Q3→18, Q4→24).
    """
    opened_at = _aware(opened_at)
    base = opened_at.replace(hour=0, minute=0, second=0, microsecond=0)
    d = base + dt.timedelta(hours=end_hour_utc)
    return d if d > opened_at else d + dt.timedelta(days=1)


def deadline_for(trade_type: str, opened_at: dt.datetime,
                 end_hour_utc: Optional[int] = None) -> dt.datetime:
    """The holding horizon for a tier: weekly / same-day / session end."""
    if trade_type == "swing":
        return swing_deadline(opened_at)
    if trade_type == "intrasession" and end_hour_utc is not None:
        return session_deadline(opened_at, end_hour_utc)
    return intraday_deadline(opened_at)


def evaluate_pending(state: dict, price: float, now: dt.datetime) -> dict:
    """Decide a PENDING limit's fate at ``price``/``now``: fill / cancel / wait.

    A long limit fills when price trades down to (or below) the limit; a short when
    price trades up to it. If the setup's ``expires_at`` passes unfilled, cancel.
    """
    side = state["side"].lower()
    long = side in ("long", "buy")
    limit = float(state["limit_price"])
    touched = price <= limit if long else price >= limit
    if touched:
        return {"action": "fill", "fill_price": limit,
                "note": f"limit touched at {limit}"}
    expires = state.get("expires_at")
    if expires is not None and now >= _aware(expires):
        return {"action": "cancel", "note": "limit expired unfilled"}
    return {"action": "wait", "note": "waiting for the limit"}


def _r(entry: float, exit_price: float, risk: float, long: bool) -> float:
    if risk <= 0:
        return 0.0
    return round(((exit_price - entry) if long else (entry - exit_price)) / risk, 4)


# Tiers that ride the session-to-session hold (exit at the next pre-London CBDR
# deviation), vs the intraday scalps that keep their own tight time-stop.
HOLD_TYPES = ("swing", "sniper", "prelondon", "crt")


def prelondon_exit(state: dict, levels: dict, price: float) -> Optional[dict]:
    """Hands-off session-hold exit: close a held trade at the next pre-London CBDR
    deviation level in its favour — a LONG at the +1SD (then +1.5SD) sell-limit,
    a SHORT at the −1SD (then −1.5SD) buy-limit. Returns a close action or None.

    ``levels`` is a CBDR box's level dict (needs the ±1SD / ±1.5SD keys). This is
    the mechanical "ride the trend to the next pre-London range, no emotion" rule.
    """
    if not levels:
        return None
    long = state.get("side") == "long"
    entry = float(state["entry"])
    risk = abs(entry - float(state.get("stop_initial", state.get("stop", entry))))
    if long:
        deep, first = levels.get("+1.5SD"), levels.get("+1SD")
        if deep is not None and price >= deep:
            hit, tag = deep, "PRELONDON+1.5SD"
        elif first is not None and price >= first:
            hit, tag = first, "PRELONDON+1SD"
        else:
            return None
    else:
        deep, first = levels.get("-1.5SD"), levels.get("-1SD")
        if deep is not None and price <= deep:
            hit, tag = deep, "PRELONDON-1.5SD"
        elif first is not None and price <= first:
            hit, tag = first, "PRELONDON-1SD"
        else:
            return None
    return {"close": True, "exit_price": round(hit, 2), "exit_reason": tag,
            "result_r": _r(entry, hit, risk, long)}


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

    # Time-stop: not at target by the tier's horizon → close at market. Uses the
    # position's explicit deadline (intraday/session), else the weekly deadline.
    deadline = state.get("deadline")
    opened_at = state.get("opened_at")
    if deadline is None and opened_at is not None:
        deadline = swing_deadline(opened_at)
    if deadline is not None and now >= _aware(deadline):
        out.update(close=True, exit_price=price, exit_reason="TIME",
                   result_r=_r(entry, price, risk, long),
                   note=f"time-stop ({state.get('trade_type', 'swing')} horizon)")
        return out

    out["note"] = (f"holding — TP{tp_hit} reached" if tp_hit else "holding — no target yet")
    return out


# --- lifecycle alert narration ----------------------------------------------

def format_lifecycle_events(events, symbol: str = "XAU/USD"):
    """Telegram card narrating position transitions (armed→filled→TP/BE→closed),
    each with running or realized R. Returns None if there is nothing to report."""
    if not events:
        return None
    icon = {"filled": "🎯", "tp": "✅", "breakeven": "🛡️", "closed": "🏁",
            "cancelled": "🚫"}
    lines = [f"📟 *GOLD Trade Lifecycle — {symbol}*", ""]
    for e in events:
        k = e.get("kind")
        tt = e.get("trade_type", "")
        side = (e.get("side") or "").upper()
        if k == "filled":
            lines.append(f"{icon[k]} FILLED {side} {tt} @ {e.get('entry')}  (SL {e.get('stop')})")
        elif k == "tp":
            lines.append(f"{icon[k]} TP{e.get('tp_hit')} {side} {tt}  ·  running {e.get('running_r'):+}R")
        elif k == "breakeven":
            lines.append(f"{icon[k]} STOP→BE {side} {tt}  ·  locked, running {e.get('running_r'):+}R")
        elif k == "closed":
            r = e.get("result_r")
            r_str = f"{r:+}R" if r is not None else "—"
            lines.append(f"{icon[k]} CLOSED {side} {tt} @ {e.get('exit_price')} "
                         f"({e.get('exit_reason')})  ·  {r_str}")
        elif k == "cancelled":
            lines.append(f"{icon[k]} CANCELLED {tt} — {e.get('reason', '')}")
    return "\n".join(lines)
