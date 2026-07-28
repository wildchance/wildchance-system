"""Portfolio optimisation — conviction-scaled, risk-budgeted allocation (Phase 11).

Given the fleet accounts and a signal (entry/stop), size each account so that (a) every
account risks a controlled % of its OWN balance — equal-risk-contribution, not equal-lot
— and (b) the risk is SCALED by the VAULTUM conviction (Kelly-lite: full risk at high
conviction, floored at 25% when the read is weak). Then it checks the resulting book
against the portfolio risk budget so the fleet never over-commits on a marginal signal.

Pure + stdlib-only. The route feeds live conviction from the VAULTUM board.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

XAU_CONTRACT = 100.0          # 1.00 lot XAU/USD = 100 oz


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def conviction_scale(conviction_pct: float) -> float:
    """Map a 0-100 conviction into a risk multiplier. Floors at 0.25 (never zero size on
    a live signal) and reaches 1.0 at full conviction — the Kelly-lite risk dial."""
    return round(0.25 + 0.75 * _clamp(conviction_pct / 100.0, 0.0, 1.0), 3)


def allocate(accounts: Sequence[dict], entry: float, stop: float,
             conviction_pct: float = 60.0, base_risk_pct: float = 1.0,
             max_risk_pct: float = 2.0, contract: float = XAU_CONTRACT,
             min_lot: float = 0.01) -> dict:
    """Per-account risk-budgeted sizing for one signal, scaled by conviction.

    Each account risks ``risk_pct × conviction_scale`` of its balance (capped at
    ``max_risk_pct``); the lot is the money-first size over the stop distance."""
    scale = conviction_scale(conviction_pct)
    stop_dist = abs(float(entry) - float(stop))
    legs: List[dict] = []
    total_risk = total_notional = total_balance = 0.0
    for a in accounts:
        bal = float(a.get("balance", a.get("default_deposit", 0)) or 0)
        rp = _clamp(float(a.get("risk_pct", base_risk_pct)) * scale, 0.0, max_risk_pct)
        risk_usd = round(bal * rp / 100.0, 2)
        lot = round(risk_usd / (stop_dist * contract), 2) if stop_dist > 0 else 0.0
        if risk_usd > 0 and stop_dist > 0:
            lot = max(lot, min_lot)
        notional = round(lot * contract * float(entry), 2)
        legs.append({"account": a.get("id", a.get("account")), "balance": bal,
                     "risk_pct": round(rp, 3), "risk_usd": risk_usd,
                     "lot": lot, "notional": notional})
        total_risk += risk_usd
        total_notional += notional
        total_balance += bal
    return {
        "conviction_pct": conviction_pct, "conviction_scale": scale,
        "stop_distance": round(stop_dist, 2), "entry": float(entry), "stop": float(stop),
        "legs": legs, "accounts": len(legs),
        "total_risk_usd": round(total_risk, 2), "total_notional": round(total_notional, 2),
        "total_balance": round(total_balance, 2),
        "portfolio_risk_pct": round(total_risk / total_balance * 100, 2) if total_balance else None,
        "method": "equal-risk-contribution × conviction (Kelly-lite)",
    }


def risk_budget_check(alloc: dict, limit_pct: float = 3.0) -> dict:
    """Is the fleet's total risk within the aggregate budget? Scales every leg down
    proportionally if it isn't, so the book stays inside the budget."""
    pr = alloc.get("portfolio_risk_pct")
    if pr is None:
        return {**alloc, "within_budget": True, "budget_pct": limit_pct,
                "note": "no balance — budget check skipped"}
    if pr <= limit_pct:
        return {**alloc, "within_budget": True, "budget_pct": limit_pct,
                "note": f"fleet risk {pr:.1f}% within {limit_pct:.1f}% budget"}
    factor = limit_pct / pr
    scaled = []
    for leg in alloc["legs"]:
        scaled.append({**leg, "risk_usd": round(leg["risk_usd"] * factor, 2),
                       "lot": round(leg["lot"] * factor, 2),
                       "notional": round(leg["notional"] * factor, 2)})
    return {**alloc, "legs": scaled, "within_budget": False, "budget_pct": limit_pct,
            "scale_factor": round(factor, 3),
            "total_risk_usd": round(alloc["total_risk_usd"] * factor, 2),
            "portfolio_risk_pct": limit_pct,
            "note": (f"fleet risk {pr:.1f}% EXCEEDS {limit_pct:.1f}% budget — every leg "
                     f"scaled ×{factor:.2f} to fit")}


def optimise(accounts: Sequence[dict], entry: float, stop: float,
             conviction_pct: float = 60.0, budget_pct: float = 3.0,
             base_risk_pct: float = 1.0, max_risk_pct: float = 2.0) -> dict:
    """Full pass: conviction-scaled allocation, then the aggregate risk-budget fit."""
    alloc = allocate(accounts, entry, stop, conviction_pct=conviction_pct,
                     base_risk_pct=base_risk_pct, max_risk_pct=max_risk_pct)
    return risk_budget_check(alloc, limit_pct=budget_pct)
