"""HTF-ORB conviction scaling — structure leans the SIZE, never unlocks a trade.

The safe way to let higher-timeframe order-block structure have a voice without
re-opening the premature-long bleed the DXY lock closed:

  • it only ever scales the SIZE of a trade that is ALREADY allowed;
  • only RANGE-FADE tiers are eligible (sniper / prelondon / intrasession / crt /
    sd_fade) — the mean-reversion entries the DXY lock already permits;
  • trend tiers (swing / intraday) are NEVER touched, so a premature trend long
    can neither be sized up nor unlocked; the hard DXY gate still owns them;
  • the bump only fires when the trade's side AGREES with a FRESH HTF ORB bias,
    and is capped (default ×1.35, max ×1.5).

So a fresh monthly/weekly bullish ORB gives your range-fade longs a little more
size — it can't fire a trade the macro says isn't ready.
"""

from __future__ import annotations

from typing import Tuple

from gold.risk_engine import _round_lot, MIN_LOT

# Only these tiers may be conviction-scaled — the mean-reversion / limit entries.
RANGE_FADE_TIERS = frozenset({"sniper", "prelondon", "intrasession", "crt",
                              "sd_fade", "limit"})
# Trend tiers are explicitly NEVER scaled (belt-and-braces).
TREND_TIERS = frozenset({"swing", "intraday"})

DEFAULT_AGREE_MULT = 1.35
MAX_MULT = 1.5


def _side(signal_or_side) -> str:
    return "long" if str(signal_or_side).lower() in ("long", "buy", "l") else "short"


def conviction_multiplier(signal, trade_type: str, htf_orb_bias: str,
                          agree_mult: float = DEFAULT_AGREE_MULT) -> Tuple[float, str]:
    """The size multiplier for one candidate given the fresh HTF ORB bias.

    Returns (multiplier, reason). 1.0 = no bump. Only a range-fade tier whose side
    matches a directional HTF ORB bias is scaled; everything else stays 1.0."""
    if trade_type in TREND_TIERS:
        return 1.0, "trend tier — never conviction-scaled (DXY gate owns it)"
    if trade_type not in RANGE_FADE_TIERS:
        return 1.0, "tier not eligible for conviction scaling"
    if htf_orb_bias not in ("long", "short"):
        return 1.0, "no HTF ORB bias"
    if _side(signal) != htf_orb_bias:
        return 1.0, f"side opposes HTF ORB {htf_orb_bias} — no bump"
    mult = min(float(agree_mult), MAX_MULT)
    return mult, f"HTF ORB {htf_orb_bias} agrees — conviction ×{mult}"


def apply_conviction(card: dict, mult: float) -> dict:
    """Return a copy of the candidate with lot + risk scaled by ``mult`` (tagged).

    Never shrinks (mult ≤ 1 is a no-op) and never bypasses the exposure cap — the
    scaled risk_usd flows into allocate()'s cap check, so an over-sized bump simply
    won't fit rather than breaching the budget."""
    if mult <= 1.0:
        return card
    out = dict(card)
    if out.get("lot"):
        out["lot"] = max(MIN_LOT, _round_lot(float(out["lot"]) * mult))
    if out.get("risk_usd"):
        out["risk_usd"] = round(float(out["risk_usd"]) * mult, 2)
    out["conviction_mult"] = round(mult, 3)
    return out
