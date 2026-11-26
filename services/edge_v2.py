"""EdgeFinder v2 — one −10..+10 score per market with a transparent factor grid.

  Technical  trend (daily + 4H EMA structure, ±1 each) · seasonality (±1)
  Sentiment  COT (±2) · crowd / retail contrarian (±2)          — strength matrix legs
  Macro      growth · inflation · jobs · rates, per indicator (±2) — services.macro_surprise
             pair = base − quote · gold/silver = −USD · US indices = +USD growth/jobs, −inflation
  Carry      policy-rate differential (shown, not scored)

Prices come from the free pool (never the TwelveData budget). The board is cached for
an hour and snapshotted daily for the score-vs-price history chart.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List, Optional

from services import macro_surprise as ms

FX = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "NZD/USD",
      "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "GBP/AUD", "EUR/NZD", "GBP/CHF", "GBP/CAD")
OTHER = ("XAU/USD", "XAG/USD", "US30", "NAS100", "SPX500")
UNIVERSE = FX + OTHER
MACRO_COLS = [k for keys in ms.CATEGORIES.values() for k in keys]
BANK = {"USD": "FED", "EUR": "ECB", "GBP": "BOE", "JPY": "BOJ", "CAD": "BOC", "AUD": "RBA", "CHF": "SNB", "NZD": "RBNZ"}
CACHE_S = 3600
HIST_KEY = "edge2_history"
_cache: Dict = {"t": 0.0, "v": None}


def clip(x: float, lo: int = -2, hi: int = 2) -> int:
    return int(max(lo, min(hi, round(x))))


def bias(score: int) -> str:
    return ("Very Bullish" if score >= 7 else "Bullish" if score >= 3 else
            "Very Bearish" if score <= -7 else "Bearish" if score <= -3 else "Neutral")


def legs(sym: str):
    s = sym.replace("/", "")
    return (s[:3], s[3:6]) if len(s) == 6 and s[:3] in ms.CCYS and s[3:6] in ms.CCYS else None


def macro_for(sym: str, state: Dict) -> Dict[str, int]:
    """Per-indicator macro votes for one market (±2 each)."""
    lg = legs(sym)
    usd = ms.indicators(state, "USD")
    out = {}
    for k in MACRO_COLS:
        if lg:
            a, b = ms.indicators(state, lg[0]).get(k, 0), ms.indicators(state, lg[1]).get(k, 0)
            out[k] = clip(a - b)
        elif sym.startswith(("XAU", "XAG")):
            out[k] = clip(-usd.get(k, 0))                          # strong US data = headwind for metals
        else:                                                      # US indices
            cat = next(c for c, ks in ms.CATEGORIES.items() if k in ks)
            sign = -1 if cat in ("inflation", "rates") else 1
            out[k] = clip(sign * usd.get(k, 0))
    return out


def trend_vote(closes: List[float]) -> int:
    """EMA20 vs EMA50 and close vs EMA20 agree → ±1, else 0."""
    if len(closes) < 50:
        return 0

    def ema(xs, n):
        k, e = 2 / (n + 1), xs[0]
        for x in xs[1:]:
            e = x * k + e * (1 - k)
        return e
    e20, e50, c = ema(closes, 20), ema(closes, 50), closes[-1]
    if c > e20 > e50:
        return 1
    if c < e20 < e50:
        return -1
    return 0


async def _closes(sym: str, interval: str, n: int) -> List[float]:
    from services.price_pool import alt_values
    try:
        d = await alt_values(sym, interval, n)
        return [float(v["close"]) for v in reversed((d or {}).get("values") or [])]
    except Exception:
        return []


def sentiment_for(sym: str, comp: Dict[str, Dict[str, float]], feed: Optional[Dict]) -> Dict[str, int]:
    """COT and crowd votes from the strength-matrix legs (gold: its own feed row)."""
    lg = legs(sym)
    if lg:
        def diff(part):
            c = comp.get(part) or {}
            if lg[0] not in c or lg[1] not in c:
                return 0
            return clip((c[lg[0]] - c[lg[1]]) * 2)
        return {"cot": diff("cot"), "crowd": diff("retail")}
    if sym.startswith("XAU"):
        row = next((s for s in (feed or {}).get("signals") or [] if s.get("pair") == "XAU/USD"), None)
        if row:
            from edgefinder.engine import _cot_factor, _retail_factor
            return {"cot": clip(_cot_factor(row)["score"]), "crowd": clip(_retail_factor(row)["score"])}
    return {"cot": 0, "crowd": 0}


def carry(sym: str, rates: Dict[str, float]) -> Optional[Dict]:
    lg = legs(sym)
    if not lg:
        return None
    a, b = rates.get(BANK[lg[0]]), rates.get(BANK[lg[1]])
    if a is None or b is None:
        return None
    d = round(a - b, 2)
    return {"base_rate": a, "quote_rate": b, "diff": d,
            "earns": "BUY" if d > 0.05 else "SELL" if d < -0.05 else "flat"}


def row(sym: str, tech: Dict[str, int], sent: Dict[str, int], macro: Dict[str, int],
        cry: Optional[Dict] = None) -> Dict:
    raw = sum(tech.values()) + sum(sent.values()) + sum(macro.values())
    score = clip(raw, -10, 10)
    cats = {c: sum(macro.get(k, 0) for k in ks) for c, ks in ms.CATEGORIES.items()}
    return {"symbol": sym, "score": score, "bias": bias(score), "raw": raw, "technical": tech,
            "sentiment": sent, "macro": macro, "categories": cats, "carry": cry}


async def board(force: bool = False) -> Dict:
    if not force and _cache["v"] and time.time() - _cache["t"] < CACHE_S:
        return _cache["v"]
    await ms.ingest()
    state = await ms.load()
    try:
        from services import fx_rotation
        comp = fx_rotation.components(await fx_rotation._momentum(), await _feed(), await fx_rotation._pc_ratios())
    except Exception:
        comp = {}
    feed = await _feed()
    try:
        from services.free_macro_feeds import POLICY_RATES, bis_policy_rates
        rates = (await bis_policy_rates()).get("rates") or dict(POLICY_RATES)
    except Exception:
        rates = {}
    rows = []
    for sym in UNIVERSE:
        d1 = await _closes(sym, "1day", 80)
        h4 = await _closes(sym, "4h", 80)
        season = await _season(sym)
        tech = {"trend_d1": trend_vote(d1), "trend_h4": trend_vote(h4), "seasonality": season}
        r = row(sym, tech, sentiment_for(sym, comp, feed), macro_for(sym, state), carry(sym, rates))
        r["close"] = d1[-1] if d1 else None
        rows.append(r)
    rows.sort(key=lambda r: -abs(r["score"]))
    out = {"rows": rows, "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
           "macro_updated": state.get("_updated"),
           "surprise_index": {c: ms.surprise_index(state, c) for c in ms.CCYS},
           "currency_macro": {c: ms.category_scores(state, c) for c in ms.CCYS}}
    _cache.update(t=time.time(), v=out)
    try:
        from services import data_store
        await data_store.put("edge2_board", out)
    except Exception:
        pass
    return out


async def cached() -> Optional[Dict]:
    """The last built board (memory, else the stored copy) — never triggers a rebuild."""
    if _cache["v"]:
        return _cache["v"]
    try:
        from services import data_store
        v, _ = await data_store.get("edge2_board")
        if v:
            _cache.update(t=time.time() - CACHE_S + 600, v=v)   # reuse, refresh within 10 min
        return v
    except Exception:
        return None


_season_mem: Dict[str, tuple] = {}


async def _season(sym: str) -> int:
    """This month's seasonal direction (±1/0), computed once per month per market (monthly
    bars cost TwelveData credits) and kept in the data store across restarts."""
    month = dt.date.today().strftime("%Y-%m")
    hit = _season_mem.get(sym)
    if hit and hit[0] == month:
        return hit[1]
    from services import data_store
    try:
        saved, _ = await data_store.get("edge2_season")
        saved = dict(saved or {})
    except Exception:
        saved = {}
    if (saved.get(sym) or [None])[0] == month:
        _season_mem[sym] = tuple(saved[sym])
        return int(saved[sym][1])
    d = 0
    try:
        from services import seasonality_service
        s = await seasonality_service.seasonal_for(sym)
        d = int((s or {}).get("dir") or 0)
    except Exception:
        pass
    _season_mem[sym] = (month, d)
    saved[sym] = [month, d]
    try:
        await data_store.put("edge2_season", saved)
    except Exception:
        pass
    return d


async def _feed() -> Optional[Dict]:
    try:
        from services.wildchance_service import get_latest_feed
        return await get_latest_feed()
    except Exception:
        return None


async def score_for(symbol: str) -> Optional[Dict]:
    """Cached row for one market (council agent / pair read); never forces a rebuild."""
    v = await cached()
    if not v:
        return None
    want = symbol.upper().replace("/", "")
    return next((r for r in v["rows"] if r["symbol"].replace("/", "") == want), None)


async def snapshot() -> Dict:
    """Daily: store score + close per market for the score-vs-price chart."""
    from services import data_store
    b = await board(force=True)
    hist, _ = await data_store.get(HIST_KEY)
    hist = dict(hist or {})
    day = dt.date.today().isoformat()
    for r in b["rows"]:
        h = [x for x in hist.get(r["symbol"], []) if x[0] != day]
        h.append([day, r["score"], r.get("close")])
        hist[r["symbol"]] = h[-365:]
    await data_store.put(HIST_KEY, hist)
    return {"stored": len(b["rows"]), "day": day}


async def history(symbol: str) -> List:
    from services import data_store
    hist, _ = await data_store.get(HIST_KEY)
    want = symbol.upper().replace("/", "")
    for k, v in (hist or {}).items():
        if k.replace("/", "") == want:
            return v
    return []


def text(b: Dict, n: int = 10) -> str:
    lines = ["🧭 EdgeFinder v2 — top setups (−10…+10)"]
    for r in b["rows"][:n]:
        c = r["categories"]
        lines.append(f"{'🟢' if r['score'] > 0 else '🔴' if r['score'] < 0 else '⚪'} {r['symbol']} {r['score']:+d} "
                     f"{r['bias']} · tech {sum(r['technical'].values()):+d} · sent {sum(r['sentiment'].values()):+d} · "
                     f"growth {c['growth']:+d} · infl {c['inflation']:+d} · jobs {c['jobs']:+d}")
    si = {k: v for k, v in b.get("surprise_index", {}).items() if v is not None}
    if si:
        lines.append("Surprise index (90 d): " + " · ".join(f"{k} {v:+d}" for k, v in sorted(si.items(), key=lambda kv: -kv[1])))
    lines.append("Bias ≠ entry — trades still need a trigger + council GO.")
    return "\n".join(lines)


def carry_text(b: Dict) -> str:
    rows = [r for r in b["rows"] if r.get("carry")]
    rows.sort(key=lambda r: -abs(r["carry"]["diff"]))
    lines = ["💱 Carry scanner (policy-rate differential — the side that earns swap)"]
    for r in rows[:10]:
        c = r["carry"]
        lines.append(f"{r['symbol']}: {c['base_rate']:.2f}% vs {c['quote_rate']:.2f}% → {c['diff']:+.2f}% · earns on {c['earns']}")
    lines.append("Broker swaps differ — check yours in MT5 Specification.")
    return "\n".join(lines)
