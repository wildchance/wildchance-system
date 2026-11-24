"""Hedge-fund style FX: currency STRENGTH matrix, EVENT ROTATION and EXPOSURE caps.

1. STRENGTH  each of the 8 majors (USD EUR GBP JPY CHF CAD AUD NZD) is scored −1…+1 from
     momentum   5-day change vs USD (USD = minus the average of the others)
     COT        leveraged-funds net positioning percentile (6-month rank)
     retail     contrarian Myfxbook-style long % per USD pair
     options    CME put/call open-interest ratio per pair (puts on XXX/YYY = XXX weak)
   then the strongest are paired against the weakest → the day's cleanest relative trades.
2. ROTATION  when a currency has a high-impact release now or within ROTATE_AHEAD_H, every pair
   carrying it steps aside and the scanners rotate to pairs WITHOUT it — ranked by the strength
   gap and by how little the calendar has measured that release moving them. Works for USD, EUR,
   GBP, JPY, CHF, CAD, AUD and NZD releases alike.
3. EXPOSURE  open tracked signals are netted per currency in risk units (BUY EUR/USD = EUR +1,
   USD −1). A new card that would push any currency beyond EXPOSURE_MAX units is blocked —
   three 'different' USD shorts are one USD bet.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from decouple import config

CCYS = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
ORDER = ("EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY")      # market quoting convention
USD_PAIRS = {"EUR": "EUR/USD", "GBP": "GBP/USD", "AUD": "AUD/USD", "NZD": "NZD/USD",
             "JPY": "USD/JPY", "CHF": "USD/CHF", "CAD": "USD/CAD"}
EXPOSURE_MAX = float(config("EXPOSURE_MAX", default="2"))
ROTATE_AHEAD_H = float(config("ROTATE_AHEAD_H", default="3"))
MIN_GAP = float(config("STRENGTH_MIN_GAP", default="0.5"))
WEIGHTS = {"momentum": 1.0, "cot": 1.0, "retail": 0.7, "options": 0.5}

_cache: Dict = {"t": 0.0, "v": None}


def pair_of(a: str, b: str) -> Tuple[str, bool]:
    """(symbol, a_is_base): EUR+USD → ('EUR/USD', True), JPY+GBP → ('GBP/JPY', False)."""
    if ORDER.index(a) < ORDER.index(b):
        return f"{a}/{b}", True
    return f"{b}/{a}", False


def legs(symbol: str) -> Optional[Tuple[str, str]]:
    s = symbol.upper().replace("/", "")
    if len(s) == 6 and s[:3] in CCYS and s[3:] in CCYS:
        return s[:3], s[3:]
    if s.startswith(("XAU", "XAG")):
        return s[:3], "USD"
    return None


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _per_ccy_from_pairs(vals: Dict[str, float]) -> Dict[str, float]:
    """Pair-level readings (+ = base strong) → per-currency averages."""
    acc: Dict[str, List[float]] = {c: [] for c in CCYS}
    for sym, v in vals.items():
        lg = legs(sym)
        if not lg or lg[0] not in acc:
            continue
        acc[lg[0]].append(v)
        acc[lg[1]].append(-v)
    return {c: sum(x) / len(x) for c, x in acc.items() if x}


def components(momentum: Dict[str, float], feed: Optional[Dict], pc_ratio: Dict[str, float]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    # momentum: % change of each currency vs USD over 5 days (1 % ≈ full score)
    if momentum:
        m = {c: _clip(v / 1.0) for c, v in momentum.items()}
        m["USD"] = _clip(-sum(m.values()) / len(m))
        out["momentum"] = m
    cot = (feed or {}).get("cot") or {}
    if cot:
        c = {}
        for code, ser in cot.items():
            if code in CCYS and ser:
                nets = [r["lev"] for r in ser]
                rank = sum(1 for x in nets if x <= ser[-1]["lev"]) / len(nets)
                c[code] = _clip((rank - 0.5) * 2)
        if c:
            c["USD"] = _clip(-sum(c.values()) / len(c))
            out["cot"] = c
    retail = (feed or {}).get("retail") or {}
    if retail:
        r = {p: _clip((50 - v.get("long_pct", 50)) / 30) for p, v in retail.items() if legs(p) and "XAU" not in p}
        out["retail"] = _per_ccy_from_pairs(r)
    if pc_ratio:
        o = {p: _clip(-math.log(max(0.05, v))) for p, v in pc_ratio.items() if v}
        out["options"] = _per_ccy_from_pairs(o)
    return out


def score(comp: Dict[str, Dict[str, float]]) -> Dict[str, Dict]:
    res = {}
    for c in CCYS:
        parts = {k: v[c] for k, v in comp.items() if c in v}
        w = sum(WEIGHTS[k] for k in parts)
        res[c] = {"score": round(sum(WEIGHTS[k] * v for k, v in parts.items()) / w, 2) if w else 0.0,
                  "parts": {k: round(v, 2) for k, v in parts.items()}}
    return dict(sorted(res.items(), key=lambda kv: -kv[1]["score"]))


def pairings(strength: Dict[str, Dict], exclude: Tuple[str, ...] = (), n: int = 4,
             min_gap: float = MIN_GAP) -> List[Dict]:
    """Strongest vs weakest (skipping excluded currencies), as tradable symbols + side."""
    out = []
    for a, b in combinations([c for c in strength if c not in exclude], 2):
        gap = strength[a]["score"] - strength[b]["score"]
        if abs(gap) < min_gap:
            continue
        strong, weak = (a, b) if gap > 0 else (b, a)
        sym, base_is_strong = pair_of(strong, weak)
        out.append({"symbol": sym, "side": "BUY" if base_is_strong else "SELL",
                    "strong": strong, "weak": weak, "gap": round(abs(gap), 2)})
    return sorted(out, key=lambda x: -x["gap"])[:n]


# ── data gathering ─────────────────────────────────────────────────────────────
async def _momentum() -> Dict[str, float]:
    from services.ohlc_service import fetch_ohlc
    out = {}
    for c, p in USD_PAIRS.items():
        try:
            b = await fetch_ohlc(p, "1day", 8)
        except Exception:
            b = []
        if len(b) >= 6:
            chg = (b[-1][4] / b[-6][4] - 1) * 100
            out[c] = chg if p.startswith(c) else -chg
    return out


async def _pc_ratios() -> Dict[str, float]:
    out = {}
    try:
        from services import cme_bulletin as cb
        for p in ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD"):
            ch = await cb.cached_fx_chain(p, max_age_days=10)
            if ch and ch.get("put_call_ratio"):
                out[f"{p[:3]}/{p[3:]}"] = ch["put_call_ratio"]
    except Exception:
        pass
    return out


async def strength(max_age_s: int = 1800) -> Dict:
    if _cache["v"] and time.time() - _cache["t"] < max_age_s:
        return _cache["v"]
    try:
        from services.wildchance_service import get_latest_feed
        feed = await get_latest_feed()
    except Exception:
        feed = None
    comp = components(await _momentum(), feed, await _pc_ratios())
    st = score(comp)
    v = {"as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "strength": st,
         "sources": sorted(comp), "pairings": pairings(st)}
    _cache.update(t=time.time(), v=v)
    return v


# ── 2. event rotation ──────────────────────────────────────────────────────────
def blocked_currencies(events: List[Dict], now: dt.datetime, ahead_h: float = ROTATE_AHEAD_H,
                       behind_min: float = 60) -> Dict[str, str]:
    from services import econ_calendar as ec
    out = {}
    for e in events:
        if not ec.is_blocking(e):
            continue
        t = dt.datetime.fromisoformat(e["time_utc"])
        if now - dt.timedelta(minutes=behind_min) <= t <= now + dt.timedelta(hours=ahead_h):
            out.setdefault(e["ccy"], f"{e['title']} {t:%H:%M} UTC")
    return out


def sensitivity(symbol: str, events: List[Dict]) -> Optional[float]:
    """Average learned move (pips) of these events on ``symbol``, if the calendar measured it."""
    from services import econ_calendar as ec
    vals = [m["avg_pips"] for e in events for k, m in ec._moves.items()
            if k == ec.move_key(e, symbol) and m.get("n")]
    return sum(vals) / len(vals) if vals else None


async def rotation(now: Optional[dt.datetime] = None) -> Dict:
    from services import econ_calendar as ec
    await ec.ensure_loaded()
    now = now or dt.datetime.now(dt.timezone.utc)
    blocked = blocked_currencies(ec._events, now)
    st = (await strength())["strength"]
    if not blocked:
        return {"blocked": {}, "rotate_to": [], "note": "no high-impact release in the window — trade the full matrix"}
    evs = [e for e in ec._events if e["ccy"] in blocked]
    cands = pairings(st, exclude=tuple(blocked), n=8, min_gap=0.3)
    for c in cands:
        c["event_move_pips"] = sensitivity(c["symbol"], evs)
    cands.sort(key=lambda c: (c["event_move_pips"] if c["event_move_pips"] is not None else 15) / 10 - c["gap"])
    return {"blocked": blocked, "rotate_to": cands[:5]}


def rotation_symbols(rot: Dict, n: int = 4) -> List[str]:
    return [c["symbol"] for c in rot.get("rotate_to", [])[:n]]


# ── 3. exposure ────────────────────────────────────────────────────────────────
def net_exposure(open_signals: List[Dict]) -> Dict[str, float]:
    net = {c: 0.0 for c in CCYS + ("XAU", "XAG")}
    for s in open_signals:
        lg = legs(s["symbol"])
        if not lg:
            continue
        sgn = 1 if s["side"] == "BUY" else -1
        net[lg[0]] = net.get(lg[0], 0) + sgn
        net[lg[1]] = net.get(lg[1], 0) - sgn
    return {k: v for k, v in net.items() if v}


def exposure_check(symbol: str, side: str, open_signals: List[Dict], cap: float = EXPOSURE_MAX) -> Dict:
    lg = legs(symbol)
    if not lg:
        return {"allow": True, "line": ""}
    net = net_exposure(open_signals)
    sgn = 1 if str(side).upper() in ("BUY", "LONG") else -1
    after = {lg[0]: net.get(lg[0], 0) + sgn, lg[1]: net.get(lg[1], 0) - sgn}
    over = {c: v for c, v in after.items() if abs(v) > cap}
    if over:
        why = ", ".join(f"{c} {'long' if v > 0 else 'short'} {abs(v):g}/{cap:g}" for c, v in over.items())
        return {"allow": False, "line": f"🧮 Exposure cap: {why} — same bet already on", "over": over}
    return {"allow": True, "line": "🧮 Exposure ok: " + ", ".join(f"{c} {v:+g}" for c, v in after.items())}


async def exposure_for(symbol: str, side: str) -> Dict:
    try:
        from services import signal_tracker
        rows = [r for r in await signal_tracker._load() if r["result"] == "open"]
    except Exception:
        rows = []
    return exposure_check(symbol, side, rows)


def card(st: Dict, rot: Optional[Dict] = None) -> str:
    lines = ["💪 *Currency strength* (momentum · COT · retail contrarian · CME put/call)"]
    lines.append(" > ".join(f"{c} {v['score']:+.2f}" for c, v in st["strength"].items()))
    if st["pairings"]:
        lines.append("Cleanest pairings: " + " · ".join(f"{p['side']} {p['symbol']} (gap {p['gap']})"
                                                       for p in st["pairings"]))
    if rot and rot.get("blocked"):
        lines.append("⏸ Event rotation — stepping aside from " + ", ".join(f"{c} ({w})" for c, w in rot["blocked"].items()))
        if rot.get("rotate_to"):
            lines.append("↪ Rotate to: " + " · ".join(
                f"{p['side']} {p['symbol']}" + (f" (moves {p['event_move_pips']:.0f}p on it)" if p.get("event_move_pips") else "")
                for p in rot["rotate_to"]))
    lines.append("_Pairs still need a trigger + a council GO._")
    return "\n".join(lines)
