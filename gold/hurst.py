"""Hurst Cycles — FLD, nominal cycle model, and VTL (pure).

The tradeable pieces of Hurst's 10 concepts:

  • FLD (Future Line of Demarcation) — price displaced forward by half a cycle.
    A price/ FLD crossover signals the cycle turn: cross UP → bullish, DOWN →
    bearish. This is the mechanical, testable version of the loose "3-day cycle".
  • Nominal model — the harmonic cycle ladder (each ≈ ×2 the last).
  • VTL (Valid Trend Line) — line across the last two cycle troughs (or peaks);
    a break flags the larger cycle turning.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

# Hurst nominal cycle wavelengths in TRADING DAYS (harmonic ×2 ladder).
NOMINAL_DAYS = [5, 10, 20, 40, 80, 160]


def nominal_model() -> dict:
    return {"days": NOMINAL_DAYS,
            "note": "harmonic ×2 ladder; short cycles (5–20d) drive intraday timing"}


def fld(prices: Sequence[float], cycle_len: int) -> List[Optional[float]]:
    """FLD series: value at t = price half a cycle earlier (None until available)."""
    half = max(1, cycle_len // 2)
    out: List[Optional[float]] = []
    for t in range(len(prices)):
        out.append(prices[t - half] if t - half >= 0 else None)
    return out


def fld_signal(prices: Sequence[float], cycle_len: int = 20) -> dict:
    """Latest price-vs-FLD state + crossover on the most recent bar."""
    half = max(1, cycle_len // 2)
    if len(prices) < half + 2:
        return {"cross": None, "position": None, "fld": None,
                "note": "not enough bars for the FLD"}
    f_now, f_prev = prices[-1 - half], prices[-2 - half]
    p_now, p_prev = prices[-1], prices[-2]
    cross = None
    if p_prev <= f_prev and p_now > f_now:
        cross = "bull"
    elif p_prev >= f_prev and p_now < f_now:
        cross = "bear"
    return {
        "cross": cross,
        "position": "above" if p_now > f_now else "below",
        "position_dir": "bull" if p_now > f_now else "bear",
        "fld": round(f_now, 4),
        "price": round(p_now, 4),
        "cycle_len": cycle_len,
    }


def _pivots(prices: Sequence[float], kind: str, left: int = 2, right: int = 2) -> List[int]:
    """Indices of local minima (kind='low') or maxima (kind='high')."""
    idx = []
    for i in range(left, len(prices) - right):
        window = prices[i - left:i + right + 1]
        if kind == "low" and prices[i] == min(window) and \
                prices[i] < min(prices[j] for j in range(i - left, i + right + 1) if j != i):
            idx.append(i)
        elif kind == "high" and prices[i] == max(window) and \
                prices[i] > max(prices[j] for j in range(i - left, i + right + 1) if j != i):
            idx.append(i)
    return idx


def vtl(prices: Sequence[float], kind: str = "low") -> Optional[dict]:
    """Valid Trend Line across the last two pivots of ``kind``. None if <2 pivots."""
    piv = _pivots(prices, kind)
    if len(piv) < 2:
        return None
    a, b = piv[-2], piv[-1]
    if b == a:
        return None
    slope = (prices[b] - prices[a]) / (b - a)
    return {"kind": kind, "i0": a, "i1": b, "slope": round(slope, 6),
            "y1": prices[b], "x1": b}


def vtl_broken(prices: Sequence[float], line: Optional[dict]) -> bool:
    """Did the latest price break the VTL? (below an up-trough line / above a down-peak line)."""
    if not line:
        return False
    t = len(prices) - 1
    projected = line["y1"] + line["slope"] * (t - line["x1"])
    if line["kind"] == "low":
        return prices[-1] < projected        # broke the rising trough line → bearish
    return prices[-1] > projected            # broke the falling peak line → bullish
