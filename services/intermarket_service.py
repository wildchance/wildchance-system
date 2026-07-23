"""Intermarket intelligence (B11) — cross-asset correlation matrix + net score.

Correlates gold's returns against the drivers that lead it — the dollar, real-yield
proxy, oil, equities, silver — and folds them into ONE net-correlation score in
[-1, 1]: positive = the intermarket complex CONFIRMS gold's move, negative = gold is
diverging (relative strength/weakness). Each asset carries its expected sign vs gold
(dollar/yields negative, silver/oil positive), so "aligned" means the live
correlation matches the textbook relationship.
"""

from __future__ import annotations

from typing import List, Optional

# Each driver: symbol + its EXPECTED correlation sign vs gold + a weight.
ASSETS = [
    {"symbol": "DXY", "sign": -1, "weight": 1.0, "label": "US Dollar (DXY)"},
    {"symbol": "US10Y", "sign": -1, "weight": 0.8, "label": "10Y Yield (proxy)"},
    {"symbol": "XAG/USD", "sign": +1, "weight": 0.8, "label": "Silver"},
    {"symbol": "WTI/USD", "sign": +1, "weight": 0.4, "label": "Oil (WTI)"},
    {"symbol": "SPX", "sign": -0.3, "weight": 0.4, "label": "S&P 500"},
]


def _corr(a: List[float], b: List[float]) -> Optional[float]:
    from correlation.engine import pearson
    n = min(len(a), len(b))
    if n < 3:
        return None
    return pearson(a[-n:], b[-n:])


async def intermarket_matrix(gold_symbol: str = "XAU/USD", interval: str = "1day",
                             bars: int = 60) -> dict:
    """Live correlation of each driver vs gold + the net-correlation score."""
    from services.ohlc_service import fetch_ohlc
    from correlation.engine import returns

    async def _series(sym):
        try:
            o = await fetch_ohlc(sym, interval, bars)
            return [r[4] for r in o] if o and len(o) >= 10 else None
        except Exception:
            return None

    gold = await _series(gold_symbol)
    if not gold:
        return {"error": "no gold series", "net_score": None}
    gret = returns(gold)

    rows, weighted, wsum = [], 0.0, 0.0
    for a in ASSETS:
        s = await _series(a["symbol"])
        if not s:
            rows.append({**a, "correlation": None, "aligned": None, "note": "no data"})
            continue
        corr = _corr(returns(s), gret)
        if corr is None:
            rows.append({**a, "correlation": None, "aligned": None, "note": "too few points"})
            continue
        exp = 1 if a["sign"] > 0 else -1
        aligned = (corr > 0) == (exp > 0)
        # contribution: corr * expected-sign, weighted → +ve when the pair behaves
        contrib = corr * exp * a["weight"]
        weighted += contrib
        wsum += a["weight"]
        rows.append({**a, "correlation": round(corr, 3), "aligned": aligned,
                     "note": f"{a['label']} {'confirms' if aligned else 'diverges'} "
                             f"(r={round(corr,2)}, expected {'+' if exp>0 else '-'})"})

    net = round(weighted / wsum, 3) if wsum else None
    if net is None:
        regime = "unknown"
    elif net >= 0.3:
        regime = "confirming"
    elif net <= -0.3:
        regime = "diverging"
    else:
        regime = "mixed"
    return {
        "gold": gold_symbol, "interval": interval, "matrix": rows,
        "net_score": net, "regime": regime, "assets_with_data": int(wsum > 0) and
        sum(1 for r in rows if r.get("correlation") is not None),
        "note": (f"intermarket {regime} (net {net}) — "
                 + {"confirming": "the complex agrees with gold's move",
                    "diverging": "gold showing relative strength/weakness vs its drivers",
                    "mixed": "no clear cross-asset lead", "unknown": "insufficient data"}[regime]),
    }
