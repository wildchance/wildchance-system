"""TP sweet-spot study — which take-profit distance actually pays, per market.

The bell curve says where price ENDS; a trade needs to know what gets HIT FIRST.
For every day in the history (both directions), with σ = that day's recent daily
volatility and the move unit = price · σ · √horizon:

    target at k · unit   vs   stop at STOP_K · unit (1σ)
    → hit-rate p(k) = share of trades that touch the target before the stop
      (same-bar touch of both counts as a STOP — conservative)
    → expectancy  E(k) = mean R per trade: +k at target, −1 at stop, and a trade that
      hits neither closes at the horizon price (its actual R) — not a full loss

The owner's sweet spot is the 68.2 %–95.4 % band → targets between 1σ and 2σ of
the expected move. best_k = the k in [1, 2] with the highest expectancy; it sizes the
sniper's TP3 for that market's own volatility (fast markets stretch, slow ones
tighten). Pure maths + a cached async loader.
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Sequence, Tuple

KS = (0.5, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
STOP_K = 1.0
VOL_N = 40


def sigma_daily(closes: Sequence[float], n: int = VOL_N) -> Optional[float]:
    """Stdev of daily log returns over the last n closes."""
    c = [x for x in closes[-(n + 1):] if x and x > 0]
    if len(c) < 10:
        return None
    r = [math.log(b / a) for a, b in zip(c, c[1:])]
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) if len(r) > 1 else None


def excursion_study(bars: Sequence[Tuple], horizon: int = 5, ks: Sequence[float] = KS,
                    stop_k: float = STOP_K, lookback: int = 2500) -> Dict:
    """bars = [(date, o, h, l, c)] oldest-first. Returns per-k hit-rate + expectancy."""
    bars = list(bars)[-(lookback + VOL_N + horizon):]
    hits = {k: 0 for k in ks}
    pnl = {k: 0.0 for k in ks}                   # summed R per trade (time-outs at horizon close)
    n = 0
    for i in range(VOL_N, len(bars) - horizon):
        sig = sigma_daily([b[4] for b in bars[i - VOL_N:i + 1]])
        if not sig:
            continue
        entry = bars[i][4]
        unit = entry * sig * math.sqrt(horizon)
        risk = stop_k * unit
        path = bars[i + 1:i + 1 + horizon]
        for side in (1, -1):
            n += 1
            stop = entry - side * risk
            for k in ks:
                tgt = entry + side * k * unit
                r = None
                for b in path:
                    hit_stop = b[3] <= stop if side > 0 else b[2] >= stop
                    hit_tgt = b[2] >= tgt if side > 0 else b[3] <= tgt
                    if hit_stop:                     # same bar → stop first (conservative)
                        r = -1.0
                        break
                    if hit_tgt:
                        hits[k] += 1
                        r = k / stop_k
                        break
                if r is None:                        # neither: closed at the horizon (time stop)
                    r = side * (path[-1][4] - entry) / risk
                pnl[k] += r
    if not n:
        return {"samples": 0, "table": [], "best_k": None}
    table = []
    for k in ks:
        table.append({"k": k, "hit_pct": round(100 * hits[k] / n, 1),
                      "expectancy_r": round(pnl[k] / n, 3)})
    sweet = [t for t in table if 1.0 <= t["k"] <= 2.0]
    best = max(sweet, key=lambda t: t["expectancy_r"]) if sweet else None
    return {"samples": n, "horizon_days": horizon, "stop_k": stop_k, "table": table,
            "best_k": best["k"] if best else None, "best": best,
            "sigma_daily_now": round(sigma_daily([b[4] for b in bars]) or 0, 5)}


_cache: Dict[str, Dict] = {}


async def study(symbol: str, horizon: int = 5) -> Optional[Dict]:
    """Cached a week (memory + Neon) — one long-history fetch per market per week."""
    key = f"tpstudy:{symbol.upper().replace('/', '')}:{horizon}"
    hit = _cache.get(key)
    if hit and time.time() - hit["_t"] < 7 * 86400:
        return hit["r"]
    from services import data_store
    import datetime as dt
    saved, at = await data_store.get(key)
    if saved and at and (dt.datetime.now(dt.timezone.utc) - at.replace(tzinfo=at.tzinfo or dt.timezone.utc)).days < 7:
        _cache[key] = {"r": saved, "_t": time.time()}
        return saved
    try:
        from services.data_feeds import daily_chain
        bars, src = await daily_chain(symbol, min_bars=500)
    except Exception:
        return None
    if len(bars) < 200:
        return None
    res = {**excursion_study(bars, horizon), "source": src, "symbol": symbol}
    _cache[key] = {"r": res, "_t": time.time()}
    await data_store.put(key, res)
    return res


def ladder(side: str, entry: float, stop: float, unit: Optional[float], best_k: Optional[float],
           structure: Sequence[Dict] = (), cap_price: Optional[float] = None) -> Dict:
    """TP1 1R · TP2 2R · TP3 in the 1σ–2σ sweet spot (a structural level there if one
    exists, else best_k·unit), all within the realistic cap. Returns targets + why."""
    sgn = 1 if side == "BUY" else -1
    risk = abs(entry - stop)
    if risk <= 0:
        return {"targets": [], "hold": "no risk distance"}
    if unit and risk > unit * STOP_K * 1.05:
        return {"targets": [], "hold": f"stop wider than the 1σ expected move ({risk:.5g} > {unit:.5g})"}

    def within_cap(p):
        return cap_price is None or (p <= cap_price if sgn > 0 else p >= cap_price)

    out = []
    for n, r in (("TP1", 1.0), ("TP2", 2.0)):
        p = entry + sgn * r * risk
        if within_cap(p):
            out.append({"name": n, "price": p, "rr": r, "level": f"{r:g}R"})
    if unit:
        lo, hi = entry + sgn * unit, entry + sgn * 2 * unit           # the 68–95 % band
        inside = [s for s in structure
                  if min(lo, hi) <= s["price"] <= max(lo, hi) and within_cap(s["price"])]
        if inside:
            s = min(inside, key=lambda s: abs(s["price"] - (entry + sgn * (best_k or 1.5) * unit)))
            p, why = s["price"], f"sweet spot · {s.get('level', 'structure')}"
        else:
            p, why = entry + sgn * (best_k or 1.5) * unit, f"sweet spot {best_k or 1.5:g}σ"
        if within_cap(p) and abs(p - entry) > 2 * risk * 1.02:
            out.append({"name": "TP3", "price": p, "rr": round(abs(p - entry) / risk, 2), "level": why})
    return {"targets": out, "hold": None if out else "no target inside the realistic cap",
            "sweet_zone": [entry + sgn * unit, entry + sgn * 2 * unit] if unit else None}
