"""DXY correlation / divergence + COT-extreme setup logic (pure, stdlib only).

Two complementary reads:

  1. Rolling correlation (`dxy_alignment`): Pearson correlation of an
     instrument's returns vs DXY returns over a window, compared against the
     instrument's expected sign (catalog `dxy_sign`). If they agree the dollar
     is *confirming* the move; if they disagree it's *diverging* — i.e. the
     instrument is showing relative strength/weakness vs the dollar.

  2. Directional confirmation (`signal_vs_dxy`): lighter — given DXY's recent
     direction and a signal's direction, is the signal trading WITH or AGAINST
     what the dollar implies? Used to rank intraweek setups with only one DXY
     fetch.

  Plus `cot_rank` — percentile rank of the latest COT net vs its history, to
  flag positioning extremes after the weekly COT ingest.
"""

from __future__ import annotations

from typing import List, Optional, Sequence


def returns(prices: Sequence[float]) -> List[float]:
    """Simple period-over-period returns."""
    out = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev:
            out.append((prices[i] - prev) / prev)
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    xs, ys = xs[:n], ys[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / ((vx ** 0.5) * (vy ** 0.5))


def dxy_alignment(instr_prices: Sequence[float], dxy_prices: Sequence[float],
                  dxy_sign: int) -> dict:
    """Rolling-correlation read of an instrument vs DXY."""
    corr = pearson(returns(instr_prices), returns(dxy_prices))
    if corr is None or dxy_sign == 0:
        return {"corr": corr, "dxy_sign": dxy_sign, "state": "n/a"}
    expected = "positive" if dxy_sign > 0 else "negative"
    actual = "positive" if corr >= 0 else "negative"
    aligned = (corr >= 0) == (dxy_sign > 0)
    return {
        "corr": round(corr, 3),
        "dxy_sign": dxy_sign,
        "expected": expected,
        "actual": actual,
        "state": "confirming" if aligned else "diverging",
    }


def dxy_direction(dxy_prices: Sequence[float], flat_threshold: float = 0.0005) -> str:
    """Recent DXY direction from first→last close (default flat band ±0.05%)."""
    if len(dxy_prices) < 2 or not dxy_prices[0]:
        return "unknown"
    chg = (dxy_prices[-1] - dxy_prices[0]) / dxy_prices[0]
    if chg > flat_threshold:
        return "up"
    if chg < -flat_threshold:
        return "down"
    return "flat"


def signal_vs_dxy(signal_dir: str, dxy_sign: int, dxy_dir: str) -> str:
    """Is a LONG/SHORT signal confirmed or contradicted by the dollar?

    expected instrument direction = dxy_dir if it moves WITH the dollar (+1),
    the inverse if it moves AGAINST (-1). Crosses (0) -> neutral.
    """
    s = signal_dir.upper()
    if s in ("BUY", "LONG"):
        s = "up"
    elif s in ("SELL", "SHORT"):
        s = "down"
    else:
        return "neutral"
    if dxy_sign == 0 or dxy_dir in ("flat", "unknown"):
        return "neutral"
    expected = dxy_dir if dxy_sign > 0 else ("down" if dxy_dir == "up" else "up")
    return "confirmed" if s == expected else "divergent"


def cot_rank(net_series: Sequence[float]) -> Optional[float]:
    """Percentile rank (0–1) of the latest COT net vs the series history."""
    if not net_series:
        return None
    latest = net_series[-1]
    return sum(1 for x in net_series if x <= latest) / len(net_series)


def cot_extreme(net_series: Sequence[float], hi: float = 0.85, lo: float = 0.15):
    """Flag COT positioning extremes. Returns (is_extreme, side, rank)."""
    r = cot_rank(net_series)
    if r is None:
        return False, "none", None
    if r >= hi:
        return True, "long_extreme", round(r, 3)
    if r <= lo:
        return True, "short_extreme", round(r, 3)
    return False, "neutral", round(r, 3)
