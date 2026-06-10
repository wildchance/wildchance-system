"""Entry Precision Engine — score every trade out of 100.

The framework scores four orthogonal components and takes a weighted sum;
a trade is only eligible when the total clears the frozen threshold:

    Trend Score        40%   200-EMA alignment (+ slope confirmation)
    Momentum Score     25%   RSI in the 55-70 sweet spot + MACD sign
    Positioning Score  20%   crowd vs smart money (contrarian)
    Volatility Score   15%   ATR compression followed by a breakout

    ENTER ONLY IF total > 80 / 100

Every sub-score returns 0-100 and is direction-aware (a LONG and a SHORT read
the same raw indicators mirror-imaged). The scorers take raw indicator values
so the engine is deterministic and unit-testable, independent of any feed.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

# --- FROZEN weights & threshold ---------------------------------------------
WEIGHTS = {
    "trend": 0.40,
    "momentum": 0.25,
    "positioning": 0.20,
    "volatility": 0.15,
}
ENTRY_THRESHOLD = 80.0   # only enter when the weighted score exceeds this

LONG, SHORT = "LONG", "SHORT"


def _norm_bias(bias: str) -> str:
    b = bias.strip().upper()
    if b in ("LONG", "BUY"):
        return LONG
    if b in ("SHORT", "SELL"):
        return SHORT
    raise ValueError(f"bias must be LONG/SHORT (or BUY/SELL), got {bias!r}")


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def trend_score(
    bias: str,
    price: float,
    ema200: float,
    ema200_slope: Optional[float] = None,
) -> float:
    """200-EMA alignment. Price on the correct side of the 200 EMA is the gate;
    a confirming EMA slope earns the rest.

    A LONG wants price ABOVE the 200 EMA and a rising EMA; a SHORT is the
    mirror. Price on the wrong side scores 0 — we do not trade against the
    primary trend.
    """
    b = _norm_bias(bias)
    aligned = (b == LONG and price > ema200) or (b == SHORT and price < ema200)
    if not aligned:
        return 0.0

    score = 60.0  # on the right side of the trend
    if ema200_slope is None:
        score += 20.0   # neutral credit when slope is unknown
    else:
        confirms = (b == LONG and ema200_slope > 0) or (b == SHORT and ema200_slope < 0)
        if confirms:
            score += 40.0
        elif ema200_slope == 0:
            score += 20.0
        # slope against the trade adds nothing
    return _clamp(score)


def _rsi_part(bias: str, rsi: float) -> float:
    """RSI sweet spot is 55-70 for a LONG (mirror 30-45 for a SHORT)."""
    b = _norm_bias(bias)
    # Map a SHORT onto the LONG scale by reflecting RSI about 50.
    r = rsi if b == LONG else 100.0 - rsi
    if 55.0 <= r <= 70.0:
        return 100.0
    if 50.0 <= r < 55.0 or 70.0 < r <= 75.0:
        return 60.0
    if 45.0 <= r < 50.0 or 75.0 < r <= 80.0:
        return 30.0
    return 0.0   # momentum not supporting the direction


def _macd_part(bias: str, macd_hist: float) -> float:
    """MACD histogram must agree with the direction (positive for LONG)."""
    b = _norm_bias(bias)
    return 100.0 if ((b == LONG) == (macd_hist > 0)) and macd_hist != 0 else 0.0


def momentum_score(bias: str, rsi: float, macd_hist: float) -> float:
    """RSI (60%) + MACD sign (40%)."""
    return _clamp(0.60 * _rsi_part(bias, rsi) + 0.40 * _macd_part(bias, macd_hist))


def positioning_score(bias: str, retail_long_pct: float) -> float:
    """Crowd vs smart money — fade the retail crowd.

    ``retail_long_pct`` is the fraction (0-1) of retail traders positioned long.
    Smart money fades a one-sided crowd, so a LONG idea scores high when retail
    is heavily SHORT, and a SHORT idea scores high when retail is heavily LONG.

    Example from the framework: USDCNH is 100% retail long -> a SELL is the
    contrarian play and scores 100.
    """
    b = _norm_bias(bias)
    if not 0.0 <= retail_long_pct <= 1.0:
        raise ValueError("retail_long_pct must be between 0 and 1")
    crowd_against_us = retail_long_pct if b == SHORT else (1.0 - retail_long_pct)
    return _clamp(crowd_against_us * 100.0)


def volatility_score(atr_now: float, atr_avg: float, breakout: bool = False) -> float:
    """ATR compression followed by a breakout.

    We want volatility coiled below its average (ATR compression) and then a
    breakout firing. Compression alone is a "coiling" setup; compression + a
    breakout is the full signal.
    """
    if atr_avg <= 0:
        raise ValueError("atr_avg must be positive")
    ratio = atr_now / atr_avg
    if ratio <= 0.80:
        base = 70.0       # tightly compressed
    elif ratio <= 1.00:
        base = 50.0       # below average
    else:
        base = 20.0       # already expanded
    if breakout:
        base += 30.0
    return _clamp(base)


@dataclass
class EntryScore:
    """The four components, the weighted total, and the go/no-go."""
    bias: str
    trend: float
    momentum: float
    positioning: float
    volatility: float
    total: float
    threshold: float
    qualifies: bool

    def to_dict(self) -> dict:
        return asdict(self)


def score_entry(
    bias: str,
    price: float,
    ema200: float,
    rsi: float,
    macd_hist: float,
    retail_long_pct: float,
    atr_now: float,
    atr_avg: float,
    breakout: bool = False,
    ema200_slope: Optional[float] = None,
) -> EntryScore:
    """Run the full Entry Precision Engine and return the verdict."""
    b = _norm_bias(bias)
    t = trend_score(b, price, ema200, ema200_slope)
    m = momentum_score(b, rsi, macd_hist)
    p = positioning_score(b, retail_long_pct)
    v = volatility_score(atr_now, atr_avg, breakout)

    total = (
        WEIGHTS["trend"] * t
        + WEIGHTS["momentum"] * m
        + WEIGHTS["positioning"] * p
        + WEIGHTS["volatility"] * v
    )
    total = round(total, 2)
    return EntryScore(
        bias=b,
        trend=round(t, 2),
        momentum=round(m, 2),
        positioning=round(p, 2),
        volatility=round(v, 2),
        total=total,
        threshold=ENTRY_THRESHOLD,
        qualifies=total > ENTRY_THRESHOLD,
    )
