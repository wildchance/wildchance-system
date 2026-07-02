"""Market Maker Methods (MMM) — weekly-cycle & peak-formation engine (pure).

A code-form of the MMM / Taylor weekly-cycle edge used for swing setups and
reversals, from the references:
  • Weekly levels   — previous week Open/High/Low/Close charted before the session
  • PFH / PFL       — Previous Frame High/Low for market-structure context
  • ADR targets     — daily range context (continuation vs stretched/late move)
  • Weekly cycle    — the 3-up / 3-down rhythm (Taylor buy / sell / short-sell day)
  • Peak formation  — swing-pivot reversal detection for tops/bottoms

Everything here is pure arithmetic over OHLC bars so a feed/scanner can drive it.
Bars are (open, high, low, close) tuples; oldest first.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence, Tuple

OHLC = Tuple[float, float, float, float]   # (open, high, low, close)


# ----------------------------------------------------------------------------
# Weekly levels & previous-frame structure
# ----------------------------------------------------------------------------
@dataclass
class Levels:
    open: float
    high: float
    low: float
    close: float
    mid: float
    range: float

    def to_dict(self) -> dict:
        return asdict(self)


def weekly_levels(prev_week: OHLC) -> Levels:
    """Previous-week O/H/L/C plus midpoint and range — the reference grid."""
    o, h, l, c = prev_week
    return Levels(open=o, high=h, low=l, close=c, mid=(h + l) / 2, range=h - l)


def previous_frame(bars: Sequence[OHLC]) -> Tuple[Optional[float], Optional[float]]:
    """PFH / PFL — highest high and lowest low of the prior frame (bars)."""
    if not bars:
        return None, None
    return max(b[1] for b in bars), min(b[2] for b in bars)


def level_context(price: float, lv: Levels) -> str:
    """Where price sits vs the weekly grid — quick structure read."""
    if price >= lv.high:
        return "above prior-week high (breakout / stretched)"
    if price <= lv.low:
        return "below prior-week low (breakdown / stretched)"
    if price >= lv.mid:
        return "upper half of prior week"
    return "lower half of prior week"


# ----------------------------------------------------------------------------
# ADR — average daily range and how much of it is used
# ----------------------------------------------------------------------------
def daily_ranges(bars: Sequence[OHLC]) -> List[float]:
    return [b[1] - b[2] for b in bars]


def adr(bars: Sequence[OHLC], n: int = 5) -> Optional[float]:
    """Average Daily Range over the last ``n`` completed days."""
    ranges = daily_ranges(bars)
    if not ranges:
        return None
    window = ranges[-n:]
    return sum(window) / len(window)


def adr_used(today: OHLC, adr_value: Optional[float]) -> Optional[float]:
    """Fraction of ADR travelled so far today (>1 == already stretched)."""
    if not adr_value:
        return None
    return (today[1] - today[2]) / adr_value


# ----------------------------------------------------------------------------
# Taylor weekly cycle — buy / sell / short-sell day
# ----------------------------------------------------------------------------
def taylor_day(prev: OHLC, today: OHLC) -> str:
    """Classify today's rhythm vs yesterday (the Taylor 3-day cycle):

      BUY DAY        — low made first then a rally (today's low < prior low,
                       close in the upper half) → look to be long
      SHORT SELL DAY — high made first then a decline (today's high > prior high,
                       close in the lower half) → look to be short
      SELL DAY       — trades at/near the prior day's high, momentum day
    """
    po, ph, pl, pc = prev
    o, h, l, c = today
    made_lower_low = l < pl
    made_higher_high = h > ph
    upper_half = c >= (h + l) / 2
    near_prior_high = h >= ph and abs(h - ph) <= 0.25 * max(ph - pl, 1e-9)

    if made_lower_low and upper_half:
        return "BUY DAY"
    if made_higher_high and not upper_half:
        return "SHORT SELL DAY"
    if near_prior_high:
        return "SELL DAY"
    return "SELL DAY" if upper_half else "SHORT SELL DAY"


def weekly_cycle_bias(weekday: int) -> str:
    """The 3-up / 3-down structural lean by weekday (0=Mon … 6=Sun).

    The classic weekly-cycle map: accumulation early, expansion up into mid-week,
    distribution and a sell-off leg, then a Friday recovery. This is a *context*
    bias, not a signal.
    """
    return {
        0: "Mon — early week, expansion up leg often begins",
        1: "Tue — up leg continuation / weekly high often forms",
        2: "Wed — pivot: up leg exhausts, down leg can start",
        3: "Thu — down leg, weekly low often forms",
        4: "Fri — recovery / short-covering into the close",
        5: "Sat — market closed",
        6: "Sun — accumulation / gap set-up for the week",
    }.get(weekday, "unknown")


# ----------------------------------------------------------------------------
# Peak formation — swing-pivot reversal detection
# ----------------------------------------------------------------------------
@dataclass
class Pivot:
    index: int
    kind: str          # "peak" | "trough"
    price: float

    def to_dict(self) -> dict:
        return asdict(self)


def swing_pivots(bars: Sequence[OHLC], left: int = 2, right: int = 2) -> List[Pivot]:
    """Fractal swing highs/lows: a bar whose high (low) is the max (min) within
    ``left`` bars before and ``right`` bars after it."""
    piv: List[Pivot] = []
    n = len(bars)
    for i in range(left, n - right):
        hi = bars[i][1]
        lo = bars[i][2]
        window = bars[i - left:i + right + 1]
        if hi == max(b[1] for b in window) and hi > max(
                b[1] for j, b in enumerate(window) if j != left):
            piv.append(Pivot(i, "peak", hi))
        elif lo == min(b[2] for b in window) and lo < min(
                b[2] for j, b in enumerate(window) if j != left):
            piv.append(Pivot(i, "trough", lo))
    return piv


def peak_formation(bars: Sequence[OHLC], left: int = 2, right: int = 2) -> dict:
    """Detect a recent peak/trough and whether a reversal candle confirms it.

    Returns the most recent confirmed pivot plus a reversal read:
      • after a PEAK, a bar that fails a new high and closes down → bearish reversal
      • after a TROUGH, a bar that fails a new low and closes up  → bullish reversal
    Combined with 3-up/3-down context this is the swing/reversal trigger.
    """
    piv = swing_pivots(bars, left, right)
    if not piv or len(bars) < 2:
        return {"pivot": None, "reversal": None}

    last = piv[-1]
    prev, cur = bars[-2], bars[-1]
    reversal = None
    if last.kind == "peak":
        # lower high + close below prior close
        if cur[1] <= prev[1] and cur[3] < prev[3]:
            reversal = "bearish_reversal (peak) — swing-short / fade the top"
    else:
        if cur[2] >= prev[2] and cur[3] > prev[3]:
            reversal = "bullish_reversal (trough) — swing-long / buy the dip"
    return {"pivot": last.to_dict(), "reversal": reversal}


# ----------------------------------------------------------------------------
# Aggregate read
# ----------------------------------------------------------------------------
def weekly_setup(symbol: str, weekday: int, prev_week: OHLC,
                 daily_bars: Sequence[OHLC], adr_n: int = 5) -> dict:
    """One MMM read: weekly grid + ADR usage + Taylor day + peak/reversal."""
    lv = weekly_levels(prev_week)
    pfh, pfl = previous_frame(daily_bars)
    a = adr(daily_bars, adr_n)
    today = daily_bars[-1] if daily_bars else None
    prev = daily_bars[-2] if len(daily_bars) >= 2 else None
    price = today[3] if today else lv.close
    return {
        "symbol": symbol,
        "weekly_levels": lv.to_dict(),
        "pfh": pfh,
        "pfl": pfl,
        "level_context": level_context(price, lv),
        "adr": round(a, 6) if a is not None else None,
        "adr_used": round(adr_used(today, a), 3) if (today and a) else None,
        "weekly_cycle_bias": weekly_cycle_bias(weekday),
        "taylor_day": taylor_day(prev, today) if (prev and today) else None,
        "peak_formation": peak_formation(daily_bars),
    }


# ----------------------------------------------------------------------------
# Confluence — turn a weekly_setup read into a directional bias + a side verdict
# ----------------------------------------------------------------------------
def directional_bias(setup: dict) -> dict:
    """Net long/short lean of an MMM read, with the reasons that drove it.

    Combines the strongest MMM signals: the Taylor day, a confirmed peak/trough
    reversal, and where price sits vs the weekly grid. ADR-used only annotates
    (a stretched move lowers conviction) — it does not flip the bias.
    """
    score = 0
    reasons: List[str] = []

    day = setup.get("taylor_day")
    if day == "BUY DAY":
        score += 2
        reasons.append("Taylor BUY DAY (low-first rally) → long")
    elif day == "SHORT SELL DAY":
        score -= 2
        reasons.append("Taylor SHORT SELL DAY (high-first decline) → short")

    rev = (setup.get("peak_formation") or {}).get("reversal") or ""
    if "bullish_reversal" in rev:
        score += 2
        reasons.append("bullish reversal off a trough → long")
    elif "bearish_reversal" in rev:
        score -= 2
        reasons.append("bearish reversal off a peak → short")

    lvl = setup.get("level_context") or ""
    if "below prior-week low" in lvl:
        score -= 1
        reasons.append("below prior-week low (breakdown)")
    elif "above prior-week high" in lvl:
        score += 1
        reasons.append("above prior-week high (breakout)")
    elif "upper half" in lvl:
        score += 1
    elif "lower half" in lvl:
        score -= 1

    stretched = None
    used = setup.get("adr_used")
    if used is not None and used >= 1.0:
        stretched = True
        reasons.append(f"ADR {used}x used — stretched/late, lower conviction")

    bias = "long" if score > 0 else "short" if score < 0 else "neutral"
    return {"bias": bias, "score": score, "stretched": stretched,
            "reasons": reasons}


def confluence(setup: Optional[dict], side: str) -> dict:
    """Does the MMM read confirm, diverge from, or stay neutral on ``side``?

    status: confirms | diverges | neutral | none
    """
    if not setup:
        return {"status": "none", "reason": "no MMM data"}
    b = directional_bias(setup)
    want = side.lower()
    want = "long" if want in ("long", "buy") else "short" if want in ("short", "sell") else want
    if b["bias"] == "neutral":
        status = "neutral"
    elif b["bias"] == want:
        status = "confirms"
    else:
        status = "diverges"
    return {
        "status": status,
        "bias": b["bias"],
        "score": b["score"],
        "stretched": b["stretched"],
        "taylor_day": setup.get("taylor_day"),
        "weekly_cycle": setup.get("weekly_cycle_bias"),
        "reasons": b["reasons"],
    }
