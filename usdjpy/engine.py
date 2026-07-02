"""USD/JPY mean-reversion signal engine — FROZEN RULES.

A faithful, code-form replica of the USDJPY_FORWARD_TEST.xlsx tracker.
Everything here mirrors the spreadsheet exactly so the forward test stays
honest. The only thing this code adds over the workbook is automation: it
does the arithmetic the yellow cells did, so a feed/scanner can replace the
once-a-day manual paste.

Spreadsheet equivalences (DAILY LOG sheet):
    D  MA20      = AVERAGE(last 20 closes)
    E  SD20      = STDEV(last 20 closes)          # Excel STDEV == sample (n-1)
    F  Z-SCORE   = (close - MA20) / SD20
    G  SIGNAL    = IF(F<=-2,"BUY",IF(F>=2,"SELL","—"))
    H  ENTRY     = close on a BUY/SELL row
    I  STOP      = BUY: close - 0.5*SD20 ; SELL: close + 0.5*SD20
    J  EXIT(d+3) = close 3 trading days after entry
    K  RESULT R  = BUY:  (exit-entry)/(entry-stop)
                   SELL: (entry-exit)/(stop-entry)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence

from decouple import config

# --- FROZEN RULES (locked 2026-06-04 — see FROZEN RULES sheet) ---------------
FROZEN_RULES = {
    "instrument": "USD/JPY",
    "timeframe": "1D (daily close)",
    "lookback": 20,                 # MA20 / SD20 window
    "z_entry_buy": -2.0,            # z <= -2.0  -> BUY (fade up)
    "z_entry_sell": 2.0,           # z >= +2.0  -> SELL (fade down)
    "stop_sd_multiple": 0.5,       # stop = 0.5 * SD20 beyond entry
    "hold_trading_days": 3,        # time-based exit at close of day +3
    "frozen_on": "2026-06-04",
    # Pass/fail decided in advance — do NOT move these.
    "min_trades_for_verdict": 20,
    "pass_profit_factor": 1.2,
    "fail_profit_factor": 1.0,
}

LOOKBACK = FROZEN_RULES["lookback"]
Z_BUY = FROZEN_RULES["z_entry_buy"]
Z_SELL = FROZEN_RULES["z_entry_sell"]
HOLD_DAYS = FROZEN_RULES["hold_trading_days"]

# USD/JPY: one pip = 0.01.
PIP = 0.01

# --- LIVE-STOP OVERLAY (opt-in — defaults reproduce the frozen backtest) ------
# The frozen R-unit stop is 0.5*SD20. For LIVE execution that can be too tight
# when SD20 is small (low-vol warm-up windows), so a 3-day hold gets wicked out
# by normal noise. These env knobs widen the *executed* stop WITHOUT touching the
# z-entry logic. Defaults keep 0.5*SD20 and no floor → identical to the frozen
# backtest unless you set them.
#   USDJPY_STOP_SD_MULT   stop = MULT * SD20        (frozen default 0.5)
#   USDJPY_MIN_STOP_PIPS  floor in pips; never tighter than this  (default 0=off)
STOP_MULT = config("USDJPY_STOP_SD_MULT", default=FROZEN_RULES["stop_sd_multiple"],
                   cast=float)
MIN_STOP_PIPS = config("USDJPY_MIN_STOP_PIPS", default=0.0, cast=float)


def _stop_distance(sd: float) -> float:
    """Price distance for the stop: MULT*SD20, floored at MIN_STOP_PIPS pips."""
    return max(STOP_MULT * sd, MIN_STOP_PIPS * PIP)


@dataclass
class Signal:
    """The decision for a single daily close (one DAILY LOG row)."""
    date: Optional[str]
    close: float
    ma20: Optional[float]
    sd20: Optional[float]
    z: Optional[float]
    action: str               # "BUY" | "SELL" | "NO TRADE"
    entry: Optional[float]
    stop: Optional[float]
    stop_pips: Optional[float]

    @property
    def is_trade(self) -> bool:
        return self.action in ("BUY", "SELL")

    def to_dict(self) -> dict:
        return asdict(self) | {"is_trade": self.is_trade}


def rolling_stats(closes: Sequence[float], lookback: int = LOOKBACK):
    """Return (ma, sd) over the last ``lookback`` closes, or (None, None).

    Uses sample standard deviation (n-1) to match Excel's STDEV exactly.
    Needs at least ``lookback`` closes — same guard as the workbook
    (=IF(COUNT(...)=20, ...)).
    """
    if len(closes) < lookback:
        return None, None
    window = list(closes[-lookback:])
    ma = statistics.fmean(window)
    sd = statistics.stdev(window)  # sample stdev == Excel STDEV
    return ma, sd


def evaluate_close(
    closes: Sequence[float],
    date: Optional[str] = None,
    lookback: int = LOOKBACK,
) -> Signal:
    """Evaluate the most recent close.

    ``closes`` is the full history of daily closes in chronological order,
    INCLUDING the close being evaluated as the last element.
    """
    if not closes:
        raise ValueError("closes must contain at least the current close")

    close = float(closes[-1])
    ma, sd = rolling_stats(closes, lookback)

    if ma is None or sd is None or sd == 0:
        return Signal(date, close, ma, sd, None, "NO TRADE", None, None, None)

    z = (close - ma) / sd

    stop_dist = _stop_distance(sd)
    if z <= Z_BUY:
        action = "BUY"
        stop = close - stop_dist
    elif z >= Z_SELL:
        action = "SELL"
        stop = close + stop_dist
    else:
        return Signal(date, close, ma, sd, z, "NO TRADE", None, None, None)

    entry = close
    stop_pips = abs(entry - stop) / PIP
    return Signal(date, close, ma, sd, z, action, entry, stop, stop_pips)


def is_new_stretch(
    entry_idx: int,
    prior_entry_indices: Sequence[int],
    hold_days: int = HOLD_DAYS,
) -> bool:
    """One position per stretch event (the validated NON-OVERLAPPING backtest).

    The backtest scanned with ``i = end + 1`` — after a trade entered at index i
    and exited at i+hold_days, scanning resumed at i+hold_days+1. So a daily
    BUY/SELL is only a NEW trade if no earlier trade's hold window still covers
    it; while z stays beyond the band within ``hold_days`` of the last entry,
    that day is a continuation of the same stretch, not a new bet.

    ``entry_idx`` and ``prior_entry_indices`` are positions in the chronological
    daily-log. Returns True if a new trade may open at ``entry_idx``.
    """
    for p in prior_entry_indices:
        if p < entry_idx and (entry_idx - p) <= hold_days:
            return False
    return True


def result_r(action: str, entry: float, stop: float, exit_price: float) -> float:
    """R outcome, exactly as the K column.

    R is measured in units of the risk distance (entry->stop = 0.5*SD20).
    A clean stop-out is -1R; a move equal to the risk distance in your favour
    is +1R.
    """
    if action == "BUY":
        return (exit_price - entry) / (entry - stop)
    if action == "SELL":
        return (entry - exit_price) / (stop - entry)
    raise ValueError(f"action must be BUY or SELL, got {action!r}")


def realized_r(result_r_value: Optional[float], was_stop_breached: bool) -> Optional[float]:
    """Stop-realism R: what the trade would have returned if the stop actually
    closed the position.

    The workbook (and ``result_r``) score every trade at the day+3 close, even
    if a close blew through the stop earlier — that can overstate results vs.
    real execution. This collapses any stop-breached trade to a clean -1R (a
    stop-out), and otherwise keeps the faithful day+3 R. Lets the scoreboard
    show BOTH a workbook-faithful and a stop-aware tally before real money.
    """
    if result_r_value is None:
        return None
    return -1.0 if was_stop_breached else result_r_value


def stop_breached(action: str, stop: float, closes_during_hold: Sequence[float]) -> bool:
    """Did any daily close during the hold breach the stop?

    The workbook notes: "If stop is hit intraday before day 3, the trade is a
    loss at the stop." With daily data we can only check closes, so this is an
    informational flag — it does NOT change the scoreboard R (which stays
    faithful to the workbook's day+3-close exit). Surfacing it lets you see
    how often the stop would have been touched.
    """
    for c in closes_during_hold:
        if action == "BUY" and c <= stop:
            return True
        if action == "SELL" and c >= stop:
            return True
    return False


@dataclass
class Scoreboard:
    """Mirror of the SCOREBOARD sheet."""
    trades_completed: int
    wins: int
    losses: int
    win_rate: Optional[float]
    total_r: float
    expectancy_r: Optional[float]
    gross_win_r: float
    gross_loss_r: float
    profit_factor: Optional[float]
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_scoreboard(r_results: Sequence[float]) -> Scoreboard:
    """Compute the live tally from the list of completed-trade R outcomes."""
    completed = [float(r) for r in r_results if r is not None]
    n = len(completed)
    wins = sum(1 for r in completed if r > 0)
    losses = sum(1 for r in completed if r <= 0)
    total_r = sum(completed)
    gross_win = sum(r for r in completed if r > 0)
    gross_loss = sum(r for r in completed if r <= 0)

    win_rate = (wins / n) if n else None
    expectancy = (total_r / n) if n else None
    profit_factor = abs(gross_win / gross_loss) if (n and gross_loss != 0) else None

    verdict = _verdict(n, profit_factor, total_r)

    return Scoreboard(
        trades_completed=n,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_r=total_r,
        expectancy_r=expectancy,
        gross_win_r=gross_win,
        gross_loss_r=gross_loss,
        profit_factor=profit_factor,
        verdict=verdict,
    )


def _verdict(n: int, profit_factor: Optional[float], total_r: float) -> str:
    """Pre-committed verdict logic (C14 of SCOREBOARD). Thresholds frozen."""
    min_n = FROZEN_RULES["min_trades_for_verdict"]
    pf_pass = FROZEN_RULES["pass_profit_factor"]
    pf_fail = FROZEN_RULES["fail_profit_factor"]
    if n < min_n:
        return f"INCONCLUSIVE (<{min_n} trades)"
    if profit_factor is not None and profit_factor > pf_pass and total_r > 0:
        return "PASS — tiny real allocation OK"
    if profit_factor is not None and profit_factor < pf_fail:
        return "FAIL — abandon"
    return "INCONCLUSIVE — keep on demo"
