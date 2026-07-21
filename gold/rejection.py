"""Sweep-and-reject confirmation — turn a level TOUCH into a TAKEN trade (pure).

A touch of a level (the +1.5SD sell-limit / OB extreme) is the location; the
TRIGGER is the rejection: price pokes THROUGH the level (grabs the liquidity) then
CLOSES BACK INSIDE. That close-back-inside is the confirmation the system waits for
before firing — no chasing the first tag.

  SELL level L : a bar whose HIGH ≥ L (swept the buy-side liquidity) but CLOSE < L
                 (rejected back below) → confirmed SHORT.
  BUY level L  : a bar whose LOW ≤ L but CLOSE > L → confirmed LONG.

Feed recent lower-timeframe (M15/H1) bars + the level; it returns the confirmed
entry with the stop beyond the swept extreme (the wick), within R:R.
"""

from __future__ import annotations

from typing import Optional, Sequence

_STOP_BUFFER = 1.5          # stop beyond the rejection wick (gold price)


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
    return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))


def sweep_reject(bars: Sequence, level: float, side: str,
                 lookback: int = 3, require_body: bool = True) -> Optional[dict]:
    """Most recent sweep-and-reject of ``level`` in the last ``lookback`` closed bars.

    ``side`` is the trade side wanted: "short" watches a sell level (sweep above,
    close below); "long" watches a buy level (sweep below, close above). Returns a
    confirmed entry card or None. ``require_body`` also wants the rejection candle to
    close in the trade direction (bearish body for a short)."""
    if not bars or level is None:
        return None
    short = side.lower() in ("short", "sell")
    window = list(bars)[-lookback:]
    for bar in reversed(window):           # most-recent first
        o, h, l, c = _ohlc(bar)
        if short:
            swept = h >= level
            rejected = c < level
            body_ok = (c <= o) if require_body else True
            if swept and rejected and body_ok:
                stop = round(h + _STOP_BUFFER, 2)      # above the sweep wick
                return {"confirmed": True, "signal": "SHORT", "level": round(level, 2),
                        "sweep_high": round(h, 2), "close": round(c, 2),
                        "entry": round(c, 2), "stop": stop,
                        "note": f"sweep+reject SHORT: pierced {round(level,2)} to "
                                f"{round(h,2)}, closed back at {round(c,2)}"}
        else:
            swept = l <= level
            rejected = c > level
            body_ok = (c >= o) if require_body else True
            if swept and rejected and body_ok:
                stop = round(l - _STOP_BUFFER, 2)      # below the sweep wick
                return {"confirmed": True, "signal": "LONG", "level": round(level, 2),
                        "sweep_low": round(l, 2), "close": round(c, 2),
                        "entry": round(c, 2), "stop": stop,
                        "note": f"sweep+reject LONG: pierced {round(level,2)} to "
                                f"{round(l,2)}, closed back at {round(c,2)}"}
    return None


def build_reject_card(rej: dict, targets: Sequence[float], trade_type: str = "sniper",
                      profile: str = "sweep_reject") -> dict:
    """Turn a confirmed rejection into a signal card (entry/stop/targets) for
    limit_orders.size_limit / gold_positions.open_from_signal."""
    side = "short" if rej["signal"] == "SHORT" else "long"
    dist = abs(rej["entry"] - rej["stop"]) or 1e-9
    tps = []
    for p in targets:
        if p is None:
            continue
        rr = round(abs(p - rej["entry"]) / dist, 2)
        tps.append({"price": round(p, 2), "rr": rr})
    return {"signal": rej["signal"], "side": side, "instrument": "XAU/USD",
            "kind": "market", "trade_type": trade_type,
            "entry": rej["entry"], "stop": rej["stop"], "targets": tps,
            "profile": profile, "justification": rej["note"],
            "confirmation": "sweep_reject"}
