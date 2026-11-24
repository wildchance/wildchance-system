"""Dealer options levels — free, from CBOE's delayed option chains.

Paid 'dealer gamma' services sell levels like "hedge wall", "dealer floor", "inventory
ceiling". They come from options open interest (OI) and gamma, which CBOE publishes
free (15-min delayed) at cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json.

For the next expiries (≤ OPT_MAX_DAYS) per strike: call OI, put OI and dealer gamma
exposure GEX = gamma·OI·100·S²·1% (calls +, puts −, the usual dealer-short-puts /
long-calls convention). Levels, mapped to the traded symbol by the live price ratio
(GLD → XAU/USD, QQQ/NDX → NAS100, DIA → US30, SPX → SPX500):

  call wall          strike with the most call OI above price  → "inventory ceiling"
  hedge wall         2nd-largest call OI above price            → "immediate hedge wall"
  gamma flip         where cumulative GEX turns sign            → "dealer reclaim gate"
  max pain           strike where option holders lose most      → expiry magnet
  put wall           most put OI below price                    → "primary hedge floor"
  absorption shelf   2nd-largest put OI below price
  expected move      ±ATM IV·S·√(days/365)                      → "liquidity exhaustion"
"""

from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Dict, List, Optional, Tuple

from decouple import config

MAX_DAYS = int(config("OPT_MAX_DAYS", default="45"))
PROXY = {"XAUUSD": "GLD", "XAGUSD": "SLV", "NAS100": "_NDX", "US100": "_NDX", "US30": "DIA",
         "SPX500": "_SPX", "US500": "_SPX", "SPX": "_SPX", "US2000": "_RUT",
         # currencies — CBOE-listed currency ETFs (thin OI: rougher walls than index/gold)
         "EURUSD": "FXE", "GBPUSD": "FXB", "AUDUSD": "FXA", "USDJPY": "FXY", "USDCHF": "FXF",
         "USDCAD": "FXC", "DXY": "UUP",
         # commodities
         "USOIL": "USO", "WTI": "USO", "UKOIL": "BNO", "BRENT": "BNO", "NATGAS": "UNG", "COPPER": "CPER",
         # crypto → Deribit (the real BTC/ETH options market), ETF chains as fallback
         "BTCUSD": "DERIBIT:BTC", "ETHUSD": "DERIBIT:ETH"}
# ETFs that move OPPOSITE to the traded pair (FXY = JPY per USD inverse of USD/JPY)
INVERSE = {"USDJPY", "USDCHF", "USDCAD"}
OCC = re.compile(r"^([A-Z]+?)(\d{6})([CP])(\d{8})$")


def underlying_for(symbol: str) -> str:
    s = symbol.upper().replace("/", "")
    return PROXY.get(s, s)


def parse_chain(data: Dict, today: Optional[dt.date] = None, max_days: int = MAX_DAYS) -> Dict:
    """CBOE delayed-quotes JSON → per-strike call/put OI + GEX for near expiries."""
    today = today or dt.date.today()
    d = (data or {}).get("data") or {}
    spot = d.get("current_price") or d.get("close")
    strikes: Dict[float, Dict] = {}
    atm_iv, best_gap, nearest_exp = None, None, None
    for o in d.get("options") or []:
        m = OCC.match(str(o.get("option", "")).lstrip("_"))
        if not m:
            continue
        exp = dt.datetime.strptime(m.group(2), "%y%m%d").date()
        days = (exp - today).days
        if days < 0 or days > max_days:
            continue
        k = int(m.group(4)) / 1000
        oi = float(o.get("open_interest") or 0)
        g = float(o.get("gamma") or 0)
        row = strikes.setdefault(k, {"call_oi": 0.0, "put_oi": 0.0, "gex": 0.0})
        if m.group(3) == "C":
            row["call_oi"] += oi
            row["gex"] += g * oi * 100 * (spot or 0) ** 2 * 0.01
        else:
            row["put_oi"] += oi
            row["gex"] -= g * oi * 100 * (spot or 0) ** 2 * 0.01
        if spot and o.get("iv"):
            gap = abs(k - spot) + days / 1000
            if best_gap is None or gap < best_gap:
                best_gap, atm_iv, nearest_exp = gap, float(o["iv"]), max(days, 1)
    return {"spot": spot, "strikes": strikes, "atm_iv": atm_iv, "days": nearest_exp}


def levels(chain: Dict) -> Dict:
    spot, st = chain.get("spot"), chain.get("strikes") or {}
    if not spot or not st:
        return {}
    ks = sorted(st)
    above = sorted((k for k in ks if k > spot), key=lambda k: -st[k]["call_oi"])
    below = sorted((k for k in ks if k < spot), key=lambda k: -st[k]["put_oi"])
    out: Dict[str, Optional[float]] = {
        "inventory_ceiling": above[0] if above else None,
        "hedge_wall": above[1] if len(above) > 1 else None,
        "primary_floor": below[0] if below else None,
        "absorption_shelf": below[1] if len(below) > 1 else None,
        "closest_strike": min(ks, key=lambda k: abs(k - spot)),
    }
    # max pain: strike minimising the total intrinsic value paid to holders
    def pain(p):
        return sum(st[k]["call_oi"] * max(0, p - k) + st[k]["put_oi"] * max(0, k - p) for k in ks)
    out["max_pain"] = min(ks, key=pain)
    # gamma flip: first strike (ascending) where cumulative GEX changes sign
    cum, prev, flip = 0.0, None, None
    for k in ks:
        cum += st[k]["gex"]
        if prev is not None and (prev < 0) != (cum < 0):
            flip = k
        prev = cum
    out["gamma_flip"] = flip
    if chain.get("atm_iv") and chain.get("days"):
        em = spot * chain["atm_iv"] * math.sqrt(chain["days"] / 365)
        out["exhaustion_high"], out["exhaustion_low"] = spot + em, spot - em
    return out


LABELS = {"inventory_ceiling": "Upper inventory ceiling (call wall)", "hedge_wall": "Immediate hedge wall",
          "gamma_flip": "Dealer reclaim gate (gamma flip)", "closest_strike": "Closest strike",
          "max_pain": "Max pain (expiry magnet)", "primary_floor": "Primary hedge floor (put wall)",
          "absorption_shelf": "Dealer absorption shelf", "exhaustion_high": "Liquidity exhaustion ceiling (+1 EM)",
          "exhaustion_low": "Liquidity exhaustion floor (−1 EM)"}


def mapped(lv: Dict, ratio: float, inverse: bool = False, und_spot: Optional[float] = None,
           live: Optional[float] = None, dp: int = 2) -> List[Dict]:
    """Underlying-strike levels → the traded symbol's prices. Direct: price = strike ×
    (live / underlying spot). Inverse (FXY for USD/JPY): price = live × spot / strike,
    and the ceiling/floor roles swap (an ETF call wall is a pair FLOOR)."""
    out = []
    for k, v in lv.items():
        if v is None or k not in LABELS or not v:
            continue
        if inverse and und_spot and live:
            price = live * und_spot / v
            key = {"inventory_ceiling": "primary_floor", "hedge_wall": "absorption_shelf",
                   "primary_floor": "inventory_ceiling", "absorption_shelf": "hedge_wall",
                   "exhaustion_high": "exhaustion_low", "exhaustion_low": "exhaustion_high"}.get(k, k)
        else:
            price, key = v * ratio, k
        out.append({"label": LABELS[key], "price": round(price, dp), "key": key})
    return sorted(out, key=lambda x: -x["price"])


def _dp(symbol: str) -> int:
    s = symbol.upper().replace("/", "")
    return 3 if s.endswith("JPY") else 5 if (len(s) == 6 and s.isalpha() and not s.startswith(("XAU", "XAG", "BTC", "ETH"))) else 2


def parse_deribit(rows: List[Dict], today: Optional[dt.date] = None, max_days: int = MAX_DAYS) -> Dict:
    """Deribit book summaries (BTC-27DEC26-100000-C) → the same per-strike shape.
    Deribit gives OI + mark IV (no gamma in the summary), so GEX / gamma flip are skipped."""
    today = today or dt.date.today()
    strikes: Dict[float, Dict] = {}
    spot, atm_iv, best, days_atm = None, None, None, None
    for r in rows or []:
        try:
            _, exp_s, k_s, cp = r["instrument_name"].split("-")
            exp = dt.datetime.strptime(exp_s, "%d%b%y").date()
        except (KeyError, ValueError):
            continue
        days = (exp - today).days
        if days < 0 or days > max_days:
            continue
        k = float(k_s)
        spot = spot or r.get("underlying_price")
        row = strikes.setdefault(k, {"call_oi": 0.0, "put_oi": 0.0, "gex": 0.0})
        row["call_oi" if cp == "C" else "put_oi"] += float(r.get("open_interest") or 0)
        if spot and r.get("mark_iv"):
            gap = abs(k - spot) + days / 1000
            if best is None or gap < best:
                best, atm_iv, days_atm = gap, float(r["mark_iv"]) / 100, max(days, 1)
    return {"spot": spot, "strikes": strikes, "atm_iv": atm_iv, "days": days_atm}


_cache: Dict[str, Tuple[float, Dict]] = {}


async def run(symbol: str, live_price: Optional[float] = None) -> Dict:
    """Fetch (cached 30 min), compute and map the dealer levels for ``symbol``."""
    und = underlying_for(symbol)
    s = symbol.upper().replace("/", "")
    if s not in PROXY and not (s.isalpha() and len(s) <= 5):
        return {"symbol": symbol, "ok": False, "reason": "no listed options proxy (e.g. FX crosses) — CBDR levels only"}
    hit = _cache.get(und)
    chain = hit[1] if hit and time.time() - hit[0] < 1800 else None
    if chain is None:
        from services.data_feeds import _get
        if und.startswith("DERIBIT:"):
            data = await _get("deribit_options", "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                              {"currency": und.split(":")[1], "kind": "option"})
            chain = parse_deribit((data or {}).get("result") or []) if data else {}
        else:
            data = await _get("cboe_options", f"https://cdn.cboe.com/api/global/delayed_quotes/options/{und}.json")
            chain = parse_chain(data) if data else {}
        if chain.get("strikes"):
            _cache[und] = (time.time(), chain)
    lv = levels(chain)
    if not lv:
        return {"symbol": symbol, "underlying": und, "ok": False, "reason": "no option chain"}
    inverse = s in INVERSE
    ratio = (live_price / chain["spot"]) if live_price and chain.get("spot") else 1.0
    return {"symbol": symbol, "underlying": und, "ok": True, "underlying_spot": chain["spot"],
            "ratio": round(ratio, 6), "inverse": inverse, "atm_iv": chain.get("atm_iv"), "days": chain.get("days"),
            "levels": mapped(lv, ratio, inverse, chain["spot"], live_price, _dp(symbol)), "raw": lv}


def cached_levels(symbol: str, live_price: Optional[float] = None) -> List[Dict]:
    """Cache-only read for hot paths (sniper zones); empty until run() has fetched."""
    hit = _cache.get(underlying_for(symbol))
    if not hit or time.time() - hit[0] > 6 * 3600:
        return []
    lv = levels(hit[1])
    ratio = (live_price / hit[1]["spot"]) if live_price and hit[1].get("spot") else 1.0
    s = symbol.upper().replace("/", "")
    return mapped(lv, ratio, s in INVERSE, hit[1].get("spot"), live_price, _dp(symbol)) if lv else []
