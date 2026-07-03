"""Correlation-aware risk — flag when strong EdgeFinder biases stack the same bet.

Two setups on highly-correlated pairs in the SAME effective direction aren't two
independent trades — they're one oversized position. This scans the board's
strong biases, computes pairwise return correlation (the existing correlation
engine), and warns when the exposure concentrates.
"""

from __future__ import annotations

from itertools import combinations
from typing import List

from services import edgefinder_service
from services.ohlc_service import fetch_ohlc
from correlation.engine import pearson, returns


async def concentration(threshold: float = 0.7, min_score: int = 2,
                        lookback: int = 60) -> dict:
    board = (await edgefinder_service.scoreboard()).get("board", [])
    strong = [r for r in board if abs(r.get("score", 0)) >= min_score]

    series = {}
    for r in strong:
        pair = r["pair"]
        bars = await fetch_ohlc(pair, "1day", lookback)
        if len(bars) >= 20:
            series[pair] = returns([c for (_d, _o, _h, _l, c) in bars])

    warnings: List[dict] = []
    for a, b in combinations(strong, 2):
        pa, pb = a["pair"], b["pair"]
        if pa not in series or pb not in series:
            continue
        n = min(len(series[pa]), len(series[pb]))
        corr = pearson(series[pa][-n:], series[pb][-n:])
        if corr is None:
            continue
        same_dir = (a["score"] > 0) == (b["score"] > 0)
        # Same direction + positive corr → stacked. Opposite direction + negative
        # corr → also stacked (a long on one == a short on the other).
        concentrated = (same_dir and corr >= threshold) or \
                       (not same_dir and corr <= -threshold)
        if concentrated:
            warnings.append({
                "pair_a": pa, "bias_a": a["bias"],
                "pair_b": pb, "bias_b": b["bias"],
                "corr": round(corr, 2),
                "note": (f"{pa} ({a['bias']}) & {pb} ({b['bias']}) move together "
                         f"(corr {round(corr, 2)}) — these stack the same exposure. "
                         "Size them as ONE position, not two."),
            })
    warnings.sort(key=lambda w: abs(w["corr"]), reverse=True)
    return {"threshold": threshold, "min_score": min_score,
            "strong_biases": len(strong), "concentration": warnings}
