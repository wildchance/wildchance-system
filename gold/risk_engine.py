"""Gold (XAU/USD) prop-firm risk / lot / target compute engine (pure).

Encodes the handwritten gold plan as deterministic math so every signal can be
sized and target-checked the same way, and the whole thing scales from $5k to
$1M by formula. Verified against the notes' anchors:

    $5k  → $60-lot 0.01 · $120-lot 0.02 · max 0.04
    $10k → 0.02 · 0.04 · 0.08      $25k → 0.05 · 0.10 · 0.20   … up to $1M

Lot ladder (frozen for prop discipline):
    $60-target lot  = balance / 500_000
    $120-target lot = balance / 250_000   (= 2× the $60 lot)
    max lot         = balance / 125_000   (the propfirm ceiling)

Contract spec (XAU/USD): 1.0 lot = 100 oz, so a $1.00 gold move = $100 P/L per
lot (→ $1.00 per 0.01 lot). Broker-tunable via env:
    GOLD_USD_PER_POINT   $ P/L per $1.00 gold move per 1.0 lot   (default 100)
    GOLD_PIP             gold price per "pip"                    (default 0.10)
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence

from decouple import config

from propfirm.engine import risk_limits, DEFAULT_TIER

GOLD_USD_PER_POINT = config("GOLD_USD_PER_POINT", default=100.0, cast=float)
GOLD_PIP = config("GOLD_PIP", default=0.10, cast=float)

LOT60_DIVISOR = 500_000
LOT120_DIVISOR = 250_000
MAX_DIVISOR = 125_000
MIN_LOT = 0.01


def _round_lot(x: float) -> float:
    """Round DOWN to 0.01 granularity (never over-size)."""
    return math.floor(x * 100) / 100.0


def usd_per_point(lot: float) -> float:
    """P/L in USD for a $1.00 gold move at ``lot`` size."""
    return lot * GOLD_USD_PER_POINT


def lot_ladder(balance: float) -> Dict[str, float]:
    """The three frozen lot sizes for a balance."""
    return {
        "base_lot_60": max(MIN_LOT, _round_lot(balance / LOT60_DIVISOR)),
        "agg_lot_120": max(MIN_LOT, _round_lot(balance / LOT120_DIVISOR)),
        "max_lot": max(MIN_LOT, _round_lot(balance / MAX_DIVISOR)),
    }


def move_for_target(lot: float, target_usd: float) -> Dict[str, float]:
    """Gold price move (and pip equivalent) needed to hit ``target_usd`` at ``lot``."""
    per_pt = usd_per_point(lot)
    move = target_usd / per_pt if per_pt else 0.0
    return {"price_move": round(move, 2), "pips": round(move / GOLD_PIP, 1)}


def size_for_risk(entry: float, stop: float, risk_usd: float) -> float:
    """Lot that risks exactly ``risk_usd`` given the SL price distance (money-first)."""
    dist = abs(entry - stop)
    if dist <= 0:
        return 0.0
    return _round_lot(risk_usd / (dist * GOLD_USD_PER_POINT))


def targets(entry: float, stop: float, side: str,
            rr: Sequence[float] = (2, 3, 4)) -> List[dict]:
    """TP prices at reward:risk multiples of the entry→stop distance."""
    risk = abs(entry - stop)
    sign = 1 if side.lower() in ("long", "buy") else -1
    out = []
    for r in rr:
        price = round(entry + sign * r * risk, 2)
        out.append({"rr": r, "price": price,
                    "usd_per_001": round(usd_per_point(0.01) * r * risk, 2)})
    return out


def breakeven_price(entry: float, tp: float, frac: float = 0.5) -> float:
    """Price at which to trail SL to break-even (default halfway to TP)."""
    return round(entry + (tp - entry) * frac, 2)


def prop_pass_math(balance: float, tier: str = DEFAULT_TIER) -> dict:
    """How many $60 / $120 trades pass the tier's monthly target, from propfirm."""
    lim = risk_limits(balance, tier)
    monthly = lim["monthly_target_min"]
    return {
        "tier": lim["tier"],
        "monthly_target_usd": round(monthly, 2),
        "daily_target_usd": round(lim["daily_target_min"], 2),
        "daily_loss_cap_usd": round(lim["daily_loss_cap"], 2),
        "trades_at_60": math.ceil(monthly / 60) if monthly else 0,
        "trades_at_120": math.ceil(monthly / 120) if monthly else 0,
    }


def phase_plan(balance: float) -> dict:
    """The screenshot's phase ladder: base ($60-lot) & agg ($120-lot) trade counts
    to clear 6% → 12% → 18% (Payout). Per-trade $ scales with balance:
    base ≈1.2%, agg ≈2.4% (→ $60/$120 at $5k), so the counts hold at every size."""
    return {
        "per_trade_usd": {"base_60": round(balance * 0.012, 2),
                          "agg_120": round(balance * 0.024, 2)},
        "phase1_6pct": {"target_usd": round(balance * 0.06, 2),
                        "trades_base": 6, "trades_agg": 3},
        "cumulative_12pct": {"target_usd": round(balance * 0.12, 2),
                             "trades_base": 12, "trades_agg": 6},
        "payout_18pct": {"target_usd": round(balance * 0.18, 2),
                         "trades_base": 18, "trades_agg": 9},
        "note": "counts carry a buffer over each milestone → Payout",
    }


def plan(balance: float, tier: str = DEFAULT_TIER,
         risk_usd: float = 20.0) -> dict:
    """Full computed gold sheet for a balance: lots, target moves, prop math."""
    ladder = lot_ladder(balance)
    return {
        "instrument": "XAU/USD",
        "balance": balance,
        "contract": {"usd_per_point_per_lot": GOLD_USD_PER_POINT, "pip": GOLD_PIP},
        "lots": ladder,
        "target_moves": {
            "base_60": move_for_target(ladder["base_lot_60"], 60),
            "agg_120": move_for_target(ladder["agg_lot_120"], 120),
        },
        "money_risk_example": {
            "risk_usd": risk_usd,
            "note": f"lot to risk ${risk_usd} depends on SL distance — use size_for_risk(entry, stop, {risk_usd})",
        },
        "prop": prop_pass_math(balance, tier),
        "phases": phase_plan(balance),
        "rules": {
            "base_rr": "1:2 – 1:4",
            "breakeven_at": "50% to TP (move SL → entry)",
            "daily_trades": "5–6 max",
            "tue_wed_bump": "0.02 base allowed when Monday sweep + 1 of 4 weekly profiles align",
        },
    }
