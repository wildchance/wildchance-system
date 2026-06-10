"""Portfolio Construction Framework — tiers, candidates, allocations, ranking.

This module is the declarative "what we hold and why" half of the playbook.
The four conviction tiers split the book 40 / 35 / 15 / 10, and within each
tier every candidate carries a directional bias and (for Tier 1/2) a 0-10
confidence score. The numbers below are transcribed straight from the
framework so the rest of the system has a single source of truth.

    Tier 1  High Probability        40%   strong macro trend + carry + flow
    Tier 2  Medium Probability      35%   trend exists, needs confirmation
    Tier 3  Speculative Macro       15%   EM / commodity, max 3% per position
    Tier 4  Metals Book             10%   long-term metals hedge
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Candidate:
    """A single trade idea inside a tier."""
    pair: str
    bias: str                      # "LONG" | "SHORT"
    confidence: Optional[float] = None   # 0-10 conviction (Tier 1/2 only)
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Tier:
    """One conviction tier of the book."""
    number: int
    name: str
    capital_allocation: float      # fraction of total capital, e.g. 0.40
    description: str
    candidates: List[Candidate]
    # Per-position sizing caps that apply tier-wide, as a fraction of capital.
    weights: Dict[str, float] = field(default_factory=dict)
    max_position: Optional[float] = None   # Tier 3 caps each position at 3%

    def to_dict(self) -> dict:
        d = asdict(self)
        d["candidates"] = [c.to_dict() for c in self.candidates]
        return d


# --- Tier 1: High Probability (40%) -----------------------------------------
TIER1 = Tier(
    number=1,
    name="High Probability",
    capital_allocation=0.40,
    description=(
        "Strong macro trend, positive carry or central-bank divergence, "
        "institutional positioning aligned, distance from target < 100 pips."
    ),
    candidates=[
        Candidate("EURJPY", "LONG", 9.0),
        Candidate("GBPJPY", "LONG", 9.0),
        Candidate("USDJPY", "LONG", 8.5),
        Candidate("GBPCAD", "LONG", 8.0),
        Candidate("SGDJPY", "LONG", 8.0),
        Candidate("AUDNZD", "LONG", 8.0),
    ],
    weights={
        "EURJPY": 0.08,
        "GBPJPY": 0.08,
        "USDJPY": 0.06,
        "GBPCAD": 0.06,
        "SGDJPY": 0.06,
        "AUDNZD": 0.06,
    },
)

# --- Tier 2: Medium Probability (35%) ---------------------------------------
TIER2 = Tier(
    number=2,
    name="Medium Probability",
    capital_allocation=0.35,
    description="Trend exists, moderate momentum, requires confirmation.",
    candidates=[
        Candidate("EURGBP", "SHORT", 7.5),
        Candidate("NZDUSD", "LONG", 7.0),
        Candidate("EURSGD", "SHORT", 7.0),
        Candidate("GBPSEK", "LONG", 7.0),
        Candidate("EURPLN", "SHORT", 7.0),
        Candidate("EURCAD", "SHORT", 7.0),
    ],
)

# --- Tier 3: Speculative Macro Trades (15%) ---------------------------------
TIER3 = Tier(
    number=3,
    name="Speculative Macro",
    capital_allocation=0.15,
    description="Emerging-market and commodity trades. Maximum 3% per position.",
    candidates=[
        Candidate("USDMXN", "LONG"),
        Candidate("USDTRY", "LONG"),
        Candidate("EURTRY", "LONG"),
        Candidate("GBPTRY", "LONG"),
        Candidate("EURMXN", "LONG"),
    ],
    max_position=0.03,
)

# --- Tier 4: Metals Book (10%) ----------------------------------------------
TIER4 = Tier(
    number=4,
    name="Metals Book",
    capital_allocation=0.10,
    description="Long-term metals hedge.",
    candidates=[
        Candidate("XAUUSD", "LONG", note="Long-term"),
        Candidate("XAGUSD", "LONG", note="Long-term"),
        Candidate("XPTUSD", "LONG"),
        Candidate("XPDUSD", "LONG", note="Speculative"),
    ],
)

TIERS: List[Tier] = [TIER1, TIER2, TIER3, TIER4]

# Current hedge-fund ranking, highest -> lowest probability.
HEDGE_FUND_RANKING: List[str] = [
    "EURJPY Long",
    "GBPJPY Long",
    "USDJPY Long",
    "SGDJPY Long",
    "GBPCAD Long",
    "AUDNZD Long",
    "NZDUSD Long",
    "EURGBP Short",
    "USDMXN Long",
    "XAUUSD Long-term Long",
]

PORTFOLIO_SUMMARY = {
    "tier_1_high_conviction_trend": 0.40,
    "tier_2_secondary_opportunities": 0.35,
    "tier_3_emerging_market_alpha": 0.15,
    "tier_4_metals_hedge": 0.10,
    "expected_profile": {
        "win_rate": "52-60%",
        "avg_r_multiple": "2.8-4.5",
        "target_annualized_return": "25-40%",
        "max_controlled_drawdown": "<10%",
    },
}


def tier(number: int) -> Tier:
    """Return a tier by its number (1-4)."""
    for t in TIERS:
        if t.number == number:
            return t
    raise ValueError(f"no tier numbered {number}")


def all_candidates() -> List[dict]:
    """Every candidate across all tiers, tagged with its tier number."""
    out: List[dict] = []
    for t in TIERS:
        for c in t.candidates:
            out.append({"tier": t.number, **c.to_dict()})
    return out


def total_allocation() -> float:
    """Sum of the tier capital allocations — should be 1.0 (100%)."""
    return round(sum(t.capital_allocation for t in TIERS), 6)


def to_dict() -> dict:
    """The whole framework as a JSON-serialisable dict."""
    return {
        "tiers": [t.to_dict() for t in TIERS],
        "total_allocation": total_allocation(),
        "hedge_fund_ranking": HEDGE_FUND_RANKING,
        "summary": PORTFOLIO_SUMMARY,
    }
