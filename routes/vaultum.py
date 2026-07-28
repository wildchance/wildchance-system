"""VAULTUM endpoints — the institutional feature-score board + risk gate (Phase 5/6/7/10).

Assembles the standardised gold-bias board from the reads the system already computes
(DXY regime, macro cycle, real rates, COT/liquidity, volatility regime, Venom AMD),
now with LIVE market-stress (VIX) + risk-appetite (SPX) feeds and a probabilistic HMM
regime folded into the macro-cycle score. Also exposes the portfolio VaR/ES risk gate.

Every engine/feed call is best-effort: a missing input degrades its score rather than
failing the board.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from gold import vaultum_scores as vs

router = APIRouter(prefix="/vaultum", tags=["vaultum"])


async def _htf_bars():
    try:
        from services.ohlc_service import fetch_ohlc
        return await fetch_ohlc("XAU/USD", "4h", 60)
    except Exception:
        return None


async def _gather_scores() -> dict:
    scores: dict = {}
    bars = await _htf_bars()

    # --- DXY regime → dollar strength ---
    try:
        from gold import dxy as gdxy
        reg = {"sms": "weak", "bms": "strong"}.get(gdxy.dollar_regime().get("regime"), "neutral")
        scores["dollar_strength"] = vs.dollar_strength_score(regime=reg)
    except Exception:
        scores["dollar_strength"] = vs.dollar_strength_score()

    # --- macro cycle → gold bias + conviction; real-rate direction; liquidity ---
    real_rate_dir = None
    try:
        from gold import macro_cycle as mc
        rr = mc.regime_read()
        mag = abs(rr.get("confluence_score", 0))
        conviction = "high" if mag >= 3 else "medium" if mag == 2 else "low"
        scores["macro_cycle"] = vs.macro_cycle_score(gold_bias=rr.get("gold_bias"),
                                                     conviction=conviction)
        try:
            real_rate_dir = mc.INPUTS.get("real_rate_direction")
        except Exception:
            real_rate_dir = None
        liq = (rr.get("liquidity") or {}).get("state")
        scores["liquidity"] = vs.liquidity_score(
            direction={"impaired": "contracting", "normal": "expanding"}.get(liq))
    except Exception:
        scores["macro_cycle"] = vs.macro_cycle_score()
        scores["liquidity"] = vs.liquidity_score()

    # --- HMM probabilistic regime folded into macro_cycle (Phase 6) ---
    hmm = None
    try:
        from gold.hmm_regime import regime_hmm
        if bars and len(bars) >= 21:
            hmm = regime_hmm(bars)
            if hmm.get("available"):
                mc_env = scores["macro_cycle"]
                hmm_val = {"long": 68.0, "short": 32.0, "neutral": 50.0}[hmm["gold_bias"]]
                agree = (hmm_val - 50) * (mc_env["value"] - 50) >= 0
                scores["macro_cycle"] = {
                    **mc_env,
                    "value": round(mc_env["value"] * 0.6 + hmm_val * 0.4, 1),
                    "confidence": round(min(1.0, mc_env["confidence"] * (1.15 if agree else 0.9)), 2),
                    "drivers": mc_env["drivers"] + [hmm["explanation"]],
                }
    except Exception:
        hmm = None

    scores["inflation_pressure"] = vs.inflation_pressure_score(
        direction=None,
        real_rate_direction=("rising" if (real_rate_dir or "").startswith("rising")
                             else "falling" if real_rate_dir else None))

    # --- volatility regime ---
    try:
        from gold import volatility as gv
        vr = gv.vol_regime(bars) if bars and len(bars) >= 15 else {}
        atrp = gv.atr_percentile(bars) if bars and len(bars) >= 15 else None
        scores["vol_regime"] = vs.vol_regime_score(regime=vr.get("regime"), atr_pct=atrp)
    except Exception:
        scores["vol_regime"] = vs.vol_regime_score()

    # --- Venom AMD phase ---
    try:
        from gold.venom import venom_read
        scores["venom_phase"] = vs.venom_phase_score(venom_read())
    except Exception:
        scores["venom_phase"] = vs.venom_phase_score()

    # --- LIVE market stress (VIX) + risk appetite (SPX) ---
    vix = spx = risk_state = None
    try:
        from services.risk_feeds import risk_feeds
        rf = await risk_feeds()
        vix, spx, risk_state = rf.get("vix"), rf.get("spx_change_pct"), rf.get("risk_state")
    except Exception:
        pass
    dollar_reg = "strong" if scores.get("dollar_strength", {}).get("value", 50) < 40 else None
    scores["market_stress"] = vs.market_stress_score(vix=vix, dollar_regime=dollar_reg)
    scores["risk_appetite"] = vs.risk_appetite_score(state=risk_state, equity_change_pct=spx)
    return scores, hmm


@router.get("/scores")
async def scores():
    """The full VAULTUM feature-score board — every standardised 0-100 score (with its
    explainability envelope), the HMM regime, plus the blended GOLD BIAS & CONVICTION."""
    sc, hmm = await _gather_scores()
    board = vs.gold_bias_board(sc)
    board["hmm_regime"] = hmm
    board["display"] = vs.format_board(board)
    return board


@router.get("/bias")
async def bias():
    """Compact gate — direction + conviction only, for the Autobots to read."""
    sc, _ = await _gather_scores()
    b = vs.gold_bias_board(sc)
    return {"direction": b["direction"], "gold_bias": b["gold_bias"],
            "conviction_pct": b["conviction_pct"], "confidence": b["confidence"],
            "tag": b["tag"], "top_drivers": b["top_drivers"]}


@router.get("/regime")
async def regime(states: int = Query(3, ge=2, le=3)):
    """The probabilistic HMM regime read (Phase 6) on the 4H gold series."""
    from gold.hmm_regime import regime_hmm
    bars = await _htf_bars()
    if not bars or len(bars) < 21:
        return {"available": False, "reason": "insufficient bars"}
    return regime_hmm(bars, k=states)


async def _daily_returns(n: int = 40):
    try:
        from services.ohlc_service import fetch_ohlc
        bars = await fetch_ohlc("XAU/USD", "1day", n)
        closes = [float(b[4]) for b in bars] if bars else []
        return [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
    except Exception:
        return []


@router.get("/portfolio-risk")
async def portfolio_risk_endpoint(equity: float = Query(..., description="account equity USD"),
                                  limit_pct: float = Query(5.0),
                                  conf: float = Query(0.95),
                                  db: AsyncSession = Depends(get_db)):
    """Portfolio VaR / Expected-Shortfall (Phase 10) across the open gold book + the
    APPROVE/BLOCK verdict against the risk budget."""
    from gold import portfolio_risk as pr
    positions = []
    try:
        from services.gold_positions import list_positions, _to_dict
        rows = await list_positions(db, status="open")
        for r in rows:
            d = _to_dict(r) if not isinstance(r, dict) else r
            positions.append({"side": d.get("side", d.get("trade_side", "buy")),
                              "lot": d.get("lot", d.get("size", 0.01)),
                              "entry": d.get("entry", d.get("entry_price")),
                              "price": d.get("current_price", d.get("entry", d.get("entry_price")))})
    except Exception:
        pass
    returns = await _daily_returns()
    return pr.risk_gate(positions, equity=equity, returns=returns,
                        limit_pct=limit_pct, conf=conf)


@router.get("/feeds")
async def feeds():
    """Diagnostic — which VIX/SPX symbols resolve on this TwelveData tier, so you can
    verify the market_stress + risk_appetite scores are live (vs. degrading to neutral)."""
    from services.risk_feeds import feeds_diagnostic
    return await feeds_diagnostic()


@router.get("/allocate")
async def allocate(entry: float = Query(..., description="signal entry price"),
                   stop: float = Query(..., description="signal stop price"),
                   conviction: float = Query(None, description="0-100; live VAULTUM bias if omitted"),
                   budget_pct: float = Query(3.0, description="aggregate fleet risk budget %"),
                   max_risk_pct: float = Query(2.0)):
    """Phase 11 — conviction-scaled, risk-budgeted allocation across the fleet accounts.
    Uses the live VAULTUM conviction unless one is passed."""
    from gold import portfolio_opt as po
    from services import trade_executor as te
    conv = conviction
    if conv is None:
        try:
            sc, _ = await _gather_scores()
            conv = vs.gold_bias_board(sc)["conviction_pct"]
        except Exception:
            conv = 60.0
    accounts = te.fleet_accounts()
    out = po.optimise(accounts, entry=entry, stop=stop, conviction_pct=conv,
                      budget_pct=budget_pct, max_risk_pct=max_risk_pct)
    out["conviction_source"] = "live VAULTUM board" if conviction is None else "override"
    return out


@router.get("/readiness")
async def readiness(db: AsyncSession = Depends(get_db)):
    """Go-live preflight — execution mode, fleet config, VaR-gate status, feed status,
    and pending-order count. The checklist before flipping EXECUTION_ENABLED."""
    from services import trade_executor as te
    checks = {}
    checks["execution_enabled"] = te.EXECUTION_ENABLED
    checks["fleet_enabled"] = te.FLEET_ENABLED
    checks["fleet_accounts"] = len(te.fleet_accounts())
    checks["var_gate_enabled"] = te.PORTFOLIO_VAR_GATE_ENABLED
    checks["var_gate_equity_set"] = te.PORTFOLIO_EQUITY_USD > 0
    checks["var_limit_pct"] = te.PORTFOLIO_VAR_LIMIT_PCT
    try:
        from services.risk_feeds import feeds_diagnostic
        fd = await feeds_diagnostic()
        checks["market_stress_feed_live"] = fd["market_stress_live"]
        checks["risk_appetite_feed_live"] = fd["risk_appetite_live"]
    except Exception:
        checks["market_stress_feed_live"] = checks["risk_appetite_feed_live"] = None
    try:
        pending = await te.pending(db, limit=100)
        checks["pending_orders"] = len(pending)
    except Exception:
        checks["pending_orders"] = None
    mode = "LIVE" if te.EXECUTION_ENABLED else "PAPER"
    gate = ("armed" if (te.PORTFOLIO_VAR_GATE_ENABLED and te.PORTFOLIO_EQUITY_USD > 0)
            else "dormant")
    return {"mode": mode, "var_gate": gate, "checks": checks,
            "ready_to_go_live": bool(te.FLEET_ENABLED and checks["fleet_accounts"] > 0),
            "note": (f"{mode} mode; VaR gate {gate}. Flip EXECUTION_ENABLED + FLEET_ENABLED "
                     "(and arm the VaR gate) once the MT5 VPS bridge is up.")}
