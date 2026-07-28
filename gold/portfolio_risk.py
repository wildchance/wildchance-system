"""Portfolio VaR / Expected-Shortfall risk gate (Phase 10).

The institutional discipline: no trade executes unless the resulting PORTFOLIO risk is
within budget. Pure Python (no numpy). Computes Value-at-Risk and Expected Shortfall
across all open positions + the proposed order, both parametric (variance-covariance)
and historical (empirical returns), and returns an APPROVE / BLOCK verdict.

The fleet trades a single instrument (XAU/USD) across 5 accounts, so positions are
perfectly correlated — portfolio risk is driven by the NET signed exposure, which is the
honest model (netting longs against shorts, not summing gross). Extendable to a real
covariance matrix when the book goes multi-asset.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Dict

# z-multipliers for a one-sided normal VaR
_Z = {0.90: 1.2816, 0.95: 1.6449, 0.975: 1.9600, 0.99: 2.3263}
# parametric ES multiplier = phi(z)/(1-c)
_ES_MULT = {0.95: 2.0627, 0.99: 2.6652}

XAU_CONTRACT = 100.0          # 1.00 lot XAU/USD = 100 oz


def _pos_exposure(pos: dict) -> float:
    """Signed USD notional of a position. side buy=+, sell=-."""
    lot = float(pos.get("lot", pos.get("size", 0)) or 0)
    price = float(pos.get("price", pos.get("entry", 0)) or 0)
    contract = float(pos.get("contract", XAU_CONTRACT))
    sign = -1.0 if str(pos.get("side", "buy")).lower() in ("sell", "short") else 1.0
    return sign * lot * contract * price


def daily_sigma(returns: Sequence[float]) -> Optional[float]:
    r = [float(x) for x in returns if x is not None]
    if len(r) < 5:
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))


def parametric_var(net_exposure: float, sigma: float, conf: float = 0.95) -> dict:
    z = _Z.get(conf, 1.6449)
    var = abs(net_exposure) * sigma * z
    es = abs(net_exposure) * sigma * _ES_MULT.get(conf, 2.0627)
    return {"var": round(var, 2), "es": round(es, 2), "method": "parametric",
            "sigma": round(sigma, 6), "conf": conf}


def historical_var(net_exposure: float, returns: Sequence[float], conf: float = 0.95) -> Optional[dict]:
    r = sorted(float(x) for x in returns if x is not None)
    if len(r) < 20:
        return None
    # portfolio P&L for a unit move: net_exposure * return
    pnl = sorted(net_exposure * x for x in r)          # ascending (losses first)
    idx = int((1 - conf) * len(pnl))
    var_cut = pnl[idx]
    tail = pnl[:idx + 1] or [var_cut]
    es = sum(tail) / len(tail)
    return {"var": round(abs(min(var_cut, 0.0)), 2), "es": round(abs(min(es, 0.0)), 2),
            "method": "historical", "conf": conf, "n": len(r)}


def portfolio_risk(positions: Sequence[dict], returns: Optional[Sequence[float]] = None,
                   conf: float = 0.95) -> dict:
    """VaR + ES for the current book. `returns` = recent daily gold returns (for both
    the parametric sigma and the historical distribution)."""
    gross = sum(abs(_pos_exposure(p)) for p in positions)
    net = sum(_pos_exposure(p) for p in positions)
    sigma = daily_sigma(returns) if returns else None
    param = parametric_var(net, sigma, conf) if sigma else None
    hist = historical_var(net, returns, conf) if returns else None
    # use the more conservative (larger) VaR available
    cands = [d for d in (param, hist) if d]
    chosen = max(cands, key=lambda d: d["var"]) if cands else None
    return {
        "positions": len(positions),
        "gross_exposure": round(gross, 2), "net_exposure": round(net, 2),
        "net_side": "long" if net > 0 else "short" if net < 0 else "flat",
        "parametric": param, "historical": hist,
        "var": chosen["var"] if chosen else None,
        "es": chosen["es"] if chosen else None,
        "var_method": chosen["method"] if chosen else None,
        "conf": conf,
    }


def risk_gate(positions: Sequence[dict], equity: float,
              returns: Optional[Sequence[float]] = None,
              limit_pct: float = 5.0, conf: float = 0.95,
              new_order: Optional[dict] = None) -> dict:
    """APPROVE/BLOCK verdict. Portfolio VaR must be <= limit_pct of equity. Pass the
    proposed order in `new_order` to gate the book *including* the pending trade."""
    book = list(positions) + ([new_order] if new_order else [])
    risk = portfolio_risk(book, returns, conf)
    var = risk["var"]
    equity = float(equity or 0)
    if var is None or equity <= 0:
        return {"approved": True, "reason": "no VaR estimate (insufficient data) — gate open",
                "risk": risk, "var_pct": None, "limit_pct": limit_pct}
    var_pct = round(var / equity * 100, 2)
    approved = var_pct <= limit_pct
    return {
        "approved": approved,
        "var_pct": var_pct, "limit_pct": limit_pct,
        "var_usd": var, "es_usd": risk["es"], "equity": equity,
        "net_exposure": risk["net_exposure"], "net_side": risk["net_side"],
        "method": risk["var_method"], "conf": conf,
        "risk": risk,
        "reason": (f"portfolio {int(conf*100)}% VaR {var_pct:.1f}% of equity "
                   + ("within" if approved else "EXCEEDS")
                   + f" the {limit_pct:.1f}% budget"
                   + ("" if approved else " — BLOCK new risk / reduce size")),
    }
