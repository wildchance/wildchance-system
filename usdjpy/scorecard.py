"""Performance scorecard + reflection loop — pure, deterministic, no I/O.

Turns a list of realized-R trade outcomes into a performance summary, then
*reflects*: it derives a forward-looking confidence factor and a GREEN/AMBER/RED
verdict the rest of the system can read to throttle or lean into a strategy.

This is the missing feedback loop: trades close with a realized R (see
UsdJpyTrade.result_r), and until now nothing aggregated those outcomes or fed
them back. These functions are intentionally side-effect free so they are easy
to unit-test and reuse for any R-based strategy (USD/JPY today, anything later).

Vocabulary
  R           one unit of initial risk. A clean stop-out is -1R; a winner that
              makes twice its risk is +2R. Every outcome here is already in R.
  expectancy  mean R per trade — the single most important number. Positive =
              the edge makes money on average; negative = it bleeds.
  profit_factor   gross winning R / gross losing R. >1 makes money, <1 loses.
  confidence_factor   the reflection output, in [MIN_FACTOR, MAX_FACTOR]. 1.0 is
              neutral. >1 means "this edge is working, you may lean in"; <1 means
              "this edge is underperforming, size down". Sizing engines can
              multiply by it; it never silently changes frozen rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Below this many closed trades a month's stats are noise — report them but do
# not let the reflection loop move the confidence factor off neutral.
MIN_SAMPLE = 10

# Reflection factor is clamped to this band so one hot/cold month can never
# swing sizing wildly. 1.0 == no change.
MIN_FACTOR = 0.5
MAX_FACTOR = 1.5

# Verdict thresholds (expectancy in R, profit factor unitless).
GREEN_EXPECTANCY = 0.20
GREEN_PROFIT_FACTOR = 1.30
RED_PROFIT_FACTOR = 1.00


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def max_drawdown_r(rs: List[float]) -> float:
    """Largest peak-to-trough drop of the cumulative-R equity curve (>= 0)."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


@dataclass
class Scorecard:
    n: int = 0
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    win_rate: Optional[float] = None
    expectancy: Optional[float] = None         # mean R per trade
    total_r: float = 0.0
    avg_win_r: Optional[float] = None
    avg_loss_r: Optional[float] = None
    profit_factor: Optional[float] = None      # None == undefined (no losses)
    max_drawdown_r: float = 0.0
    best_r: Optional[float] = None
    worst_r: Optional[float] = None
    # Reflection outputs.
    verdict: str = "INCONCLUSIVE"              # GREEN | AMBER | RED | INCONCLUSIVE
    confidence_factor: float = 1.0
    lesson: str = ""

    def to_dict(self) -> dict:
        def r(x, nd=4):
            return round(x, nd) if isinstance(x, (int, float)) else x
        return {
            "n": self.n,
            "wins": self.wins,
            "losses": self.losses,
            "scratches": self.scratches,
            "win_rate": r(self.win_rate, 4),
            "expectancy": r(self.expectancy, 4),
            "total_r": r(self.total_r, 4),
            "avg_win_r": r(self.avg_win_r, 4),
            "avg_loss_r": r(self.avg_loss_r, 4),
            "profit_factor": r(self.profit_factor, 4),
            "max_drawdown_r": r(self.max_drawdown_r, 4),
            "best_r": r(self.best_r, 4),
            "worst_r": r(self.worst_r, 4),
            "verdict": self.verdict,
            "confidence_factor": r(self.confidence_factor, 3),
            "lesson": self.lesson,
        }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _reflect(card: Scorecard) -> Scorecard:
    """Fill verdict / confidence_factor / lesson from the computed metrics."""
    if card.n < MIN_SAMPLE:
        card.verdict = "INCONCLUSIVE"
        card.confidence_factor = 1.0
        card.lesson = (
            f"Only {card.n} closed trade(s) — need {MIN_SAMPLE}+ before trusting "
            f"the stats. Holding sizing neutral."
        )
        return card

    exp = card.expectancy or 0.0
    pf = card.profit_factor  # may be None (no losses at all)

    # Confidence factor is expectancy-driven and clamped. Positive edge leans in,
    # negative edge sizes down; one month can never move it past the band.
    card.confidence_factor = round(_clamp(1.0 + exp, MIN_FACTOR, MAX_FACTOR), 3)

    pf_ok_green = (pf is None) or (pf >= GREEN_PROFIT_FACTOR)
    pf_bad = (pf is not None) and (pf < RED_PROFIT_FACTOR)

    if exp >= GREEN_EXPECTANCY and pf_ok_green:
        card.verdict = "GREEN"
        card.lesson = (
            f"Edge working: {card.expectancy:+.2f}R/trade over {card.n} trades, "
            f"win rate {card.win_rate:.0%}. Sizing may lean in "
            f"(factor {card.confidence_factor})."
        )
    elif exp <= 0 or pf_bad:
        card.verdict = "RED"
        card.lesson = (
            f"Edge underperforming: {card.expectancy:+.2f}R/trade over {card.n} "
            f"trades. Size down (factor {card.confidence_factor}) and review the "
            f"rules before risking more."
        )
    else:
        card.verdict = "AMBER"
        card.lesson = (
            f"Marginal: {card.expectancy:+.2f}R/trade over {card.n} trades. "
            f"Hold current sizing (factor {card.confidence_factor}) and keep "
            f"collecting data."
        )
    return card


def build_scorecard(rs: List[float]) -> Scorecard:
    """Compute the full performance + reflection scorecard from realized R's."""
    rs = [float(r) for r in rs if r is not None]
    card = Scorecard(n=len(rs))
    if not rs:
        card.lesson = "No closed trades in this window."
        return card

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    scratches = [r for r in rs if r == 0]

    card.wins = len(wins)
    card.losses = len(losses)
    card.scratches = len(scratches)
    card.win_rate = card.wins / card.n
    card.total_r = sum(rs)
    card.expectancy = card.total_r / card.n
    card.avg_win_r = _mean(wins)
    card.avg_loss_r = _mean(losses)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    card.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    card.max_drawdown_r = max_drawdown_r(rs)
    card.best_r = max(rs)
    card.worst_r = min(rs)

    return _reflect(card)


def by_group(items: List[dict], key: str, r_field: str = "result_r") -> Dict[str, dict]:
    """Group trade dicts by items[key] and build a scorecard per group.

    e.g. by_group(trades, "month") -> {"2026-06": {...scorecard...}, ...}
         by_group(trades, "action") -> {"BUY": {...}, "SELL": {...}}
    """
    buckets: Dict[str, List[float]] = {}
    for it in items:
        g = it.get(key)
        if g is None:
            continue
        r = it.get(r_field)
        if r is None:
            continue
        buckets.setdefault(str(g), []).append(float(r))
    return {g: build_scorecard(rs).to_dict() for g, rs in buckets.items()}
