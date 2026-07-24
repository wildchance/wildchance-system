"""Venom — the fractal AMD (Accumulation → Manipulation → Distribution) clock.

The operator's 'power of three' cycle, read on three timeframes at once so the
CRT 1-5-9 sweep and the session/weekly/monthly context line up:

  INTRADAY (UTC-4, the 24h CBDR day):
    14:00–22:00  ASIAN   → ACCUMULATION  (range builds; load the extremes)
    22:00–06:00  LONDON  → MANIPULATION  (the stop-hunt sweep — the CRT trigger)
    06:00–14:00  NEWYORK → DISTRIBUTION  (the trend move / mark-up-down)

  WEEKLY (day of week):
    Mon ACCUMULATION · Tue MANIPULATION · Wed/Thu DISTRIBUTION · Fri REVERSAL

  MONTHLY (week of month):
    W1 ACCUMULATION · W2 MANIPULATION · W3 DISTRIBUTION · W4 CONTINUATION/REVERSAL

When the same phase stacks across timeframes — e.g. intraday MANIPULATION inside a
weekly MANIPULATION Tuesday — the CRT sweep is highest-probability and the reversal
that follows is the trade. Venom returns each timeframe's phase + the confluence and
the phase's playbook. Pure + deterministic from a datetime.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

# Intraday AMD windows (UTC-4). London wraps midnight.
INTRADAY = [
    {"phase": "accumulation", "session": "asian",   "start": 14, "end": 22},
    {"phase": "manipulation", "session": "london",  "start": 22, "end": 6},
    {"phase": "distribution", "session": "newyork", "start": 6,  "end": 14},
]
WEEKLY = {0: "accumulation", 1: "manipulation", 2: "distribution",
          3: "distribution", 4: "reversal", 5: "weekend", 6: "weekend"}   # Mon=0
MONTHLY = {1: "accumulation", 2: "manipulation", 3: "distribution",
           4: "continuation_reversal", 5: "continuation_reversal"}

PLAYBOOK = {
    "accumulation": "range builds — load the extremes (buy discount / sell premium), no chase",
    "manipulation": "the stop-hunt sweep — WAIT for it, then trade the reversal (CRT trigger)",
    "distribution": "the trend move is on — ride it toward the HTF OB",
    "reversal": "cycle turns — fade the exhausted move / new-cycle entries",
    "continuation_reversal": "late-cycle — continue the trend or catch the turn",
    "weekend": "no session",
}


def intraday_phase(hour_utc4: int) -> dict:
    for w in INTRADAY:
        s, e = w["start"], w["end"]
        inside = (s <= hour_utc4 < e) if s < e else (hour_utc4 >= s or hour_utc4 < e)
        if inside:
            return dict(w)
    return {"phase": "distribution", "session": "newyork", "start": 6, "end": 14}


def week_of_month(d: _dt.date) -> int:
    return (d.day - 1) // 7 + 1


def weekly_phase(weekday: int) -> str:
    return WEEKLY.get(weekday, "distribution")


def monthly_phase(week_no: int) -> str:
    return MONTHLY.get(week_no, "continuation_reversal")


def venom_read(now: Optional[_dt.datetime] = None) -> dict:
    """The three-timeframe AMD read + confluence for ``now`` (UTC-4)."""
    now = now or (_dt.datetime.utcnow() - _dt.timedelta(hours=4))
    intr = intraday_phase(now.hour)
    wk = weekly_phase(now.weekday())
    mo = monthly_phase(week_of_month(now.date()))
    phases = [intr["phase"], wk, mo]
    # confluence: how many timeframes share a phase
    counts = {p: phases.count(p) for p in set(phases)}
    dominant = max(counts, key=counts.get)
    aligned = counts[dominant]
    conviction = "high" if aligned >= 3 else "medium" if aligned == 2 else "low"
    # the actionable phase = the intraday phase, weighted up when HTFs agree
    return {
        "as_of_utc4": now.strftime("%Y-%m-%d %H:%M"),
        "intraday": {"phase": intr["phase"], "session": intr["session"]},
        "weekly": {"weekday": now.strftime("%a"), "phase": wk},
        "monthly": {"week_of_month": week_of_month(now.date()), "phase": mo},
        "confluence": {"dominant_phase": dominant, "timeframes_aligned": aligned,
                       "conviction": conviction},
        "playbook": PLAYBOOK.get(intr["phase"], ""),
        "note": (f"{intr['session'].upper()} {intr['phase'].upper()} · "
                 f"{now.strftime('%a')} {wk} · W{week_of_month(now.date())} {mo} — "
                 f"{conviction} confluence"
                 + (f" (×{aligned} {dominant})" if aligned >= 2 else "")),
    }


def format_venom(read: dict) -> str:
    c = read["confluence"]
    icon = {"high": "🟣", "medium": "🟪", "low": "⚪"}.get(c["conviction"], "⚪")
    return (f"🐍 *VENOM — AMD clock*  {icon} {c['conviction']}\n"
            f"   intraday: {read['intraday']['session']} *{read['intraday']['phase']}*\n"
            f"   weekly: {read['weekly']['weekday']} {read['weekly']['phase']}  ·  "
            f"monthly: W{read['monthly']['week_of_month']} {read['monthly']['phase']}\n"
            f"   → {read['playbook']}")
