"""Order-flow engine — book imbalance + footprint delta. Pure, deterministic.

The deterministic core behind the order-flow repos (orderbook-tick-data, bid/ask
imbalance): given L2 book sizes or per-price buy/sell volume, quantify pressure.

  book_imbalance(bid_vol, ask_vol)   → (bid-ask)/(bid+ask) ∈ [-1, 1]
  depth_imbalance(bids, asks, N)     → imbalance over the top N levels
  classify(imbalance)                → bid_heavy/ask_heavy/balanced + long/short lean
  footprint(rows)                    → per-price delta, total delta, POC (volume peak)
  cumulative_delta(deltas)           → running cumulative delta

Bids/asks accept either raw sizes [1.2, 0.8, …] or [price, size] pairs.
No data feed needed — feed it a book snapshot (from Polygon L2, a broker, or a
POST body) and it returns the read.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Union

Level = Union[float, int, Sequence[float]]


def _size(x: Level) -> float:
    """Extract the size from a raw number or a [price, size] pair."""
    if isinstance(x, (list, tuple)):
        return float(x[1])
    return float(x)


def book_imbalance(bid_vol: float, ask_vol: float) -> float:
    """(bid - ask) / (bid + ask), clamped to [-1, 1]; 0 when both are 0."""
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return round((bid_vol - ask_vol) / total, 4)


def depth_imbalance(bids: Sequence[Level], asks: Sequence[Level],
                    levels: Optional[int] = None) -> float:
    """Imbalance across the top `levels` of each side (all levels if None)."""
    b = [_size(x) for x in bids]
    a = [_size(x) for x in asks]
    if levels:
        b, a = b[:levels], a[:levels]
    return book_imbalance(sum(b), sum(a))


def classify(imbalance: float, strong: float = 0.3) -> dict:
    """Turn an imbalance value into a directional read."""
    if imbalance >= strong:
        state, pressure = "bid_heavy", "long"
    elif imbalance <= -strong:
        state, pressure = "ask_heavy", "short"
    else:
        state, pressure = "balanced", "neutral"
    return {"imbalance": round(imbalance, 4), "state": state, "pressure": pressure}


def footprint(rows: List[dict]) -> dict:
    """Per-price footprint from [{price, buy, sell}] rows.

    Returns each level's delta (buy-sell) and volume, the total delta, and the
    Point of Control (price with the most traded volume).
    """
    levels = []
    total_delta = 0.0
    poc = None
    poc_vol = -1.0
    for r in rows:
        try:
            price = r.get("price")
            buy = float(r.get("buy", 0) or 0)
            sell = float(r.get("sell", 0) or 0)
        except (AttributeError, ValueError, TypeError):
            continue
        delta = buy - sell
        vol = buy + sell
        total_delta += delta
        if vol > poc_vol:
            poc_vol, poc = vol, price
        levels.append({"price": price, "delta": round(delta, 4),
                       "volume": round(vol, 4)})
    return {
        "levels": levels,
        "total_delta": round(total_delta, 4),
        "poc": poc,
        "poc_volume": round(poc_vol, 4) if poc_vol >= 0 else None,
        "bias": "bullish" if total_delta > 0 else ("bearish" if total_delta < 0 else "flat"),
    }


def cumulative_delta(deltas: Sequence[float]) -> List[float]:
    """Running cumulative delta series."""
    out, run = [], 0.0
    for d in deltas:
        run += float(d)
        out.append(round(run, 4))
    return out
