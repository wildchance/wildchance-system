"""COT report freshness — pure, deterministic, no I/O.

Closes the "COT is 3 days stale on release" gap by making the system *aware* of
how old the positioning data is and discounting its weight as the week ages.

CFTC Commitments of Traders cadence:
  • data is as-of the **Tuesday** of a week
  • it is released the following **Friday** (~3:30pm ET)
So a COT print is never fresher than 3 days old, and by the next Thursday it is
~10 days old — right when price has often already moved on. This module turns
"now" into the as-of date, an age in days, a stale flag, and a confidence
*discount* (1.0 freshest → DISCOUNT_FLOOR oldest) that ranking can multiply in.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

FRESH_DAYS = 3        # minimum possible age (Friday release of Tuesday data)
STALE_DAYS = 8        # older than this and we flag it stale
DECAY_DAYS = 11       # age over which the discount falls from 1.0 to the floor
DISCOUNT_FLOOR = 0.5  # confidence never discounted below this

_TUESDAY = 1          # date.weekday(): Mon=0 .. Sun=6
_RELEASE_LAG_DAYS = 3 # Tuesday -> Friday


def most_recent_tuesday(d: date) -> date:
    """The Tuesday on or before d."""
    return d - timedelta(days=(d.weekday() - _TUESDAY) % 7)


def cot_as_of(now: date) -> date:
    """As-of Tuesday of the most recently *released* COT report for `now`."""
    this_tue = most_recent_tuesday(now)
    released = this_tue + timedelta(days=_RELEASE_LAG_DAYS)
    if now >= released:
        return this_tue
    # This week's Tuesday data hasn't been released yet — use last week's.
    return this_tue - timedelta(days=7)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def freshness(now: date, as_of: Optional[date] = None) -> dict:
    """Freshness summary for the COT data effective at `now`.

    Pass `as_of` explicitly if you know the report's actual as-of date; otherwise
    it is inferred from the release calendar.
    """
    if as_of is None:
        as_of = cot_as_of(now)
    age_days = (now - as_of).days
    discount = round(
        _clamp(1.0 - max(0, age_days - FRESH_DAYS) / DECAY_DAYS, DISCOUNT_FLOOR, 1.0),
        3,
    )
    return {
        "as_of": as_of.isoformat(),
        "age_days": age_days,
        "stale": age_days >= STALE_DAYS,
        "discount": discount,        # multiply COT-driven confidence by this
    }
