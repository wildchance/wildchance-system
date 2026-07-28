"""VAULTUM endpoints — the institutional feature-score board (Phase 5 + 7).

Assembles the standardised gold-bias board from the reads the system already computes
(DXY regime, macro cycle, real rates, COT/liquidity, volatility regime, Venom AMD).
Every engine call is best-effort: a missing feed degrades its score to neutral/low
confidence rather than failing the board.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from gold import vaultum_scores as vs

router = APIRouter(prefix="/vaultum", tags=["vaultum"])


async def _gather_scores() -> dict:
    scores: dict = {}

    # --- DXY regime → dollar strength (sms=weak/bullish-gold, bms=strong/bearish-gold) ---
    try:
        from gold import dxy as gdxy
        dr = gdxy.dollar_regime()
        reg = {"sms": "weak", "bms": "strong"}.get(dr.get("regime"), "neutral")
        scores["dollar_strength"] = vs.dollar_strength_score(regime=reg)
    except Exception:
        scores["dollar_strength"] = vs.dollar_strength_score()

    # --- macro cycle → gold bias + conviction, and real-rate direction → inflation ---
    real_rate_dir = None
    try:
        from gold import macro_cycle as mc
        rr = mc.regime_read()
        score_mag = abs(rr.get("confluence_score", 0))
        conviction = "high" if score_mag >= 3 else "medium" if score_mag == 2 else "low"
        scores["macro_cycle"] = vs.macro_cycle_score(gold_bias=rr.get("gold_bias"),
                                                     conviction=conviction)
        try:
            real_rate_dir = mc.INPUTS.get("real_rate_direction")
        except Exception:
            real_rate_dir = None
        # liquidity from the OI-impairment read
        liq = (rr.get("liquidity") or {}).get("state")
        liq_dir = {"impaired": "contracting", "normal": "expanding"}.get(liq)
        scores["liquidity"] = vs.liquidity_score(direction=liq_dir)
    except Exception:
        scores["macro_cycle"] = vs.macro_cycle_score()
        scores["liquidity"] = vs.liquidity_score()

    inf_dir = None
    if real_rate_dir:
        inf_dir = "rising" if str(real_rate_dir).startswith("rising") else "falling"
    scores["inflation_pressure"] = vs.inflation_pressure_score(
        direction=None,
        real_rate_direction=("rising" if (real_rate_dir or "").startswith("rising")
                             else "falling" if real_rate_dir else None))

    # --- volatility regime (needs HTF bars) ---
    try:
        from services.ohlc_service import fetch_ohlc
        from gold import volatility as gv
        bars = await fetch_ohlc("XAU/USD", "4h", 60)
        vr = gv.vol_regime(bars) if bars and len(bars) >= 15 else {}
        atrp = gv.atr_percentile(bars) if bars and len(bars) >= 15 else None
        scores["vol_regime"] = vs.vol_regime_score(regime=vr.get("regime"), atr_pct=atrp)
    except Exception:
        scores["vol_regime"] = vs.vol_regime_score()

    # --- Venom AMD phase overlay ---
    try:
        from gold.venom import venom_read
        scores["venom_phase"] = vs.venom_phase_score(venom_read())
    except Exception:
        scores["venom_phase"] = vs.venom_phase_score()

    # --- market stress / risk appetite: no live VIX/equity feed wired → neutral ---
    scores["market_stress"] = vs.market_stress_score()
    scores["risk_appetite"] = vs.risk_appetite_score()
    return scores


@router.get("/scores")
async def scores():
    """The full VAULTUM feature-score board — every standardised 0-100 score (with its
    explainability envelope) plus the blended GOLD BIAS & CONVICTION read."""
    sc = await _gather_scores()
    board = vs.gold_bias_board(sc)
    board["display"] = vs.format_board(board)
    return board


@router.get("/bias")
async def bias():
    """Compact gate — direction + conviction only, for the Autobots to read."""
    sc = await _gather_scores()
    b = vs.gold_bias_board(sc)
    return {"direction": b["direction"], "gold_bias": b["gold_bias"],
            "conviction_pct": b["conviction_pct"], "confidence": b["confidence"],
            "tag": b["tag"], "top_drivers": b["top_drivers"]}
