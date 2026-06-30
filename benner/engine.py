"""Benner cycle countdown — Samuel Benner's 1875 "Periods When to Make Money".

A pure, deterministic macro clock. Benner published three repeating year
sequences that still get watched today:

  A — PANIC years (crashes/peaks of speculation) — pattern repeats 18, 20, 16
  B — years of GOOD TIMES, HIGH PRICES → time to SELL stocks/assets
  C — years of HARD TIMES, LOW PRICES → time to BUY and hold to the next boom

This module turns "what year is it" into the phase plus a countdown to the next
A/B/C year, so it can act as a macro timer alongside the yearly Quarterly engine.
Stdlib only — no data feed.
"""

from __future__ import annotations

from typing import List, Optional

# Published Benner-Fibonacci sequences (extended on his repeating intervals).
PANIC_YEARS: List[int] = [1927, 1945, 1965, 1981, 1999, 2019, 2035, 2053, 2071]
# Good times / high prices — SELL  (intervals 8, 9, 10 repeating)
TOP_YEARS: List[int] = [
    1926, 1935, 1945, 1953, 1962, 1972, 1980, 1989, 1999, 2007,
    2016, 2026, 2034, 2043, 2053, 2061,
]
# Hard times / low prices — BUY  (intervals 7, 11, 9 repeating)
BOTTOM_YEARS: List[int] = [
    1924, 1931, 1942, 1951, 1958, 1969, 1978, 1985, 1996, 2005,
    2012, 2023, 2032, 2039, 2050, 2059,
]


def _next_on_or_after(seq: List[int], year: int) -> Optional[int]:
    return next((y for y in seq if y >= year), None)


def _last_on_or_before(seq: List[int], year: int) -> Optional[int]:
    cands = [y for y in seq if y <= year]
    return cands[-1] if cands else None


def benner_status(year: int) -> dict:
    """Classify a year and count down to the next panic / top / bottom."""
    is_panic = year in PANIC_YEARS
    is_top = year in TOP_YEARS
    is_bottom = year in BOTTOM_YEARS

    if is_panic:
        phase, action = "panic", "PANIC year — peak of speculation; expect a top/crash. Protect capital."
    elif is_top:
        phase, action = "top", "GOOD TIMES / HIGH PRICES — distribute and SELL into strength."
    elif is_bottom:
        phase, action = "bottom", "HARD TIMES / LOW PRICES — accumulate and BUY for the next boom."
    else:
        # Between markers: lean by which boundary is nearer.
        last_top = _last_on_or_before(TOP_YEARS, year)
        last_bottom = _last_on_or_before(BOTTOM_YEARS, year)
        if (last_bottom or -9999) > (last_top or -9999):
            phase, action = "recovery", "Post-bottom recovery — accumulated lows maturing toward the next top."
        else:
            phase, action = "distribution", "Post-top distribution — risk rising toward the next bottom."

    next_a = _next_on_or_after(PANIC_YEARS, year)
    next_b = _next_on_or_after(TOP_YEARS, year)
    next_c = _next_on_or_after(BOTTOM_YEARS, year)
    return {
        "year": year,
        "phase": phase,            # panic | top | bottom | recovery | distribution
        "is_panic": is_panic,
        "is_top": is_top,
        "is_bottom": is_bottom,
        "action": action,
        "next_panic": next_a,
        "next_top": next_b,
        "next_bottom": next_c,
        "years_to_panic": (next_a - year) if next_a else None,
        "years_to_top": (next_b - year) if next_b else None,
        "years_to_bottom": (next_c - year) if next_c else None,
    }
