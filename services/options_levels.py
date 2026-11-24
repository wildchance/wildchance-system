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
         "SPX500": "_SPX", "US500": "_SPX", "SPX": "_SPX"}
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


def mapped(lv: Dict, ratio: float) -> List[Dict]:
    """Underlying-strike levels → the traded symbol's prices (e.g. GLD × ~10.9 → XAU)."""
    return sorted(({"label": LABELS[k], "price": round(v * ratio, 2), "key": k}
                   for k, v in lv.items() if v is not None and k in LABELS), key=lambda x: -x["price"])


_cache: Dict[str, Tuple[float, Dict]] = {}


async def run(symbol: str, live_price: Optional[float] = None) -> Dict:
    """Fetch (cached 30 min), compute and map the dealer levels for ``symbol``."""
    und = underlying_for(symbol)
    hit = _cache.get(und)
    chain = hit[1] if hit and time.time() - hit[0] < 1800 else None
    if chain is None:
        from services.data_feeds import _get
        data = await _get("cboe_options", f"https://cdn.cboe.com/api/global/delayed_quotes/options/{und}.json")
        chain = parse_chain(data) if data else {}
        if chain.get("strikes"):
            _cache[und] = (time.time(), chain)
    lv = levels(chain)
    if not lv:
        return {"symbol": symbol, "underlying": und, "ok": False, "reason": "no option chain"}
    ratio = (live_price / chain["spot"]) if live_price and chain.get("spot") else 1.0
    return {"symbol": symbol, "underlying": und, "ok": True, "underlying_spot": chain["spot"],
            "ratio": round(ratio, 4), "atm_iv": chain.get("atm_iv"), "days": chain.get("days"),
            "levels": mapped(lv, ratio), "raw": lv}


def cached_levels(symbol: str, live_price: Optional[float] = None) -> List[Dict]:
    """Cache-only read for hot paths (sniper zones); empty until run() has fetched."""
    hit = _cache.get(underlying_for(symbol))
    if not hit or time.time() - hit[0] > 6 * 3600:
        return []
    lv = levels(hit[1])
    ratio = (live_price / hit[1]["spot"]) if live_price and hit[1].get("spot") else 1.0
    return mapped(lv, ratio) if lv else []
