"""Free macro feeds — no-key, no-quota data (WorldMonitor-style).

Pulls the risk/macro signals VAULTUM needs from FREE public APIs, so the market_stress,
risk_appetite, jpy_liquidity and geopolitical scores go live WITHOUT burning TwelveData
quota (which the paid feed was rate-limiting):

  • Yahoo Finance chart API  (^VIX, ^GSPC, QQQ, XLP, JPY=X) — no key
  • alternative.me Fear&Greed (crypto sentiment, a risk-on/off tell) — no key
  • GDELT 2.1 DOC API         (geopolitical conflict tone) — no key

All best-effort with a 10-min in-process cache; every fetch degrades to None so a dead
feed never breaks the board. Network calls are isolated here — the scoring stays pure.
"""

from __future__ import annotations

import time
from typing import Optional, Dict, List

import httpx

_CACHE: Dict[str, tuple] = {}
_TTL = 600                              # 10-minute cache (free APIs — be polite)
_UA = {"User-Agent": "Mozilla/5.0 (WildchanceVaultum/1.0)"}
_TIMEOUT = httpx.Timeout(8.0)


def _cached(key: str):
    v = _CACHE.get(key)
    return v[0] if (v and v[1] > time.time()) else None


def _put(key: str, value):
    _CACHE[key] = (value, time.time() + _TTL)
    return value


async def yahoo_closes(symbol: str, rng: str = "3mo", interval: str = "1d") -> List[float]:
    """Daily closes for a Yahoo symbol (free chart API). [] on failure."""
    ck = f"yh:{symbol}:{rng}:{interval}"
    hit = _cached(ck)
    if hit is not None:
        return hit
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
            r = await c.get(url, params={"range": rng, "interval": interval})
            j = r.json()
        res = (((j.get("chart") or {}).get("result") or [None])[0]) or {}
        closes = (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
        closes = [float(x) for x in closes if x is not None]
        return _put(ck, closes)
    except Exception:
        return []


async def yahoo_last(symbol: str) -> Optional[float]:
    closes = await yahoo_closes(symbol, "5d", "1d")
    return closes[-1] if closes else None


def _roc(closes: List[float], n: int) -> Optional[float]:
    if len(closes) <= n or closes[-1 - n] == 0:
        return None
    return round((closes[-1] - closes[-1 - n]) / closes[-1 - n] * 100, 2)


async def fear_greed() -> Optional[int]:
    """alternative.me Crypto Fear & Greed index (0-100). A broad risk-on/off tell."""
    hit = _cached("fng")
    if hit is not None:
        return hit
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
            r = await c.get("https://api.alternative.me/fng/", params={"limit": 1})
            j = r.json()
        val = int((j.get("data") or [{}])[0].get("value"))
        return _put("fng", val)
    except Exception:
        return None


# GDELT's tonechart is slow (5-20s) and occasionally rate-limits — give it its own,
# longer timeout so it doesn't get cut off (the 8s default was too tight → null).
_GDELT_TIMEOUT = httpx.Timeout(20.0)


async def geopolitical_risk() -> Optional[float]:
    """A 0-100 geopolitical-risk score from GDELT's conflict-tone chart (free). Higher =
    more negative global tone around conflict → a safe-haven tailwind for gold."""
    hit = _cached("geo")
    if hit is not None:
        return hit
    # simpler query (no parens) + a fallback, 1-week window for a stable tonechart
    queries = ["war OR conflict OR sanctions OR military OR strike", "geopolitical risk"]
    for q in queries:
        try:
            params = {"query": q, "mode": "tonechart", "format": "json", "timespan": "1w"}
            async with httpx.AsyncClient(timeout=_GDELT_TIMEOUT, headers=_UA) as c:
                r = await c.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
            if "json" not in (r.headers.get("content-type") or "") and not r.text.strip().startswith("{"):
                continue                                   # GDELT returned an HTML error page
            bins = (r.json() or {}).get("tonechart") or []
            if not bins:
                continue
            total = sum(b.get("count", 0) for b in bins) or 1
            # weighted average tone; more negative = higher risk. Map [-10..+2] → [100..0].
            avg_tone = sum(b.get("bin", 0) * b.get("count", 0) for b in bins) / total
            score = max(0.0, min(100.0, (2.0 - avg_tone) / 12.0 * 100.0))
            return _put("geo", round(score, 1))
        except Exception:
            continue
    return None


# Current G10 policy rates (%, approx — operator-updatable via set_policy_rates()).
# Live BIS SDMX wiring is a future upgrade; this encoded map gives the divergence now.
POLICY_RATES = {"FED": 4.50, "ECB": 2.15, "BOE": 4.00, "BOJ": 0.50,
                "BOC": 2.75, "RBA": 3.60, "SNB": 0.00, "RBNZ": 3.00}


def set_policy_rates(**rates) -> dict:
    """Operator update of the policy-rate map (feed today's rates)."""
    for k, v in rates.items():
        if v is not None:
            try:
                POLICY_RATES[k.upper()] = float(v)
            except (TypeError, ValueError):
                pass
    return dict(POLICY_RATES)


# BIS "Central bank policy rates" (WS_CBPOL) — free SDMX, no key. REF_AREA → our label.
_BIS_AREA = {"US": "FED", "XM": "ECB", "GB": "BOE", "JP": "BOJ",
             "CA": "BOC", "AU": "RBA", "CH": "SNB", "NZ": "RBNZ"}
_BIS_TIMEOUT = httpx.Timeout(20.0)


async def bis_policy_rates(apply: bool = True) -> dict:
    """Best-effort LIVE policy rates from the BIS WS_CBPOL_D dataset (free SDMX-JSON, no
    key). Fetches the latest observation per central bank and, when ``apply``, merges it
    into POLICY_RATES. Degrades gracefully — any bank that fails keeps its encoded value,
    and a full outage just returns the encoded map with source='encoded'."""
    hit = _cached("bis")
    if hit is not None:
        merged = {**dict(POLICY_RATES), **{_BIS_AREA[a]: v for a, v in hit.items()}}
        if apply:
            for a, v in hit.items():
                POLICY_RATES[_BIS_AREA[a]] = v
        return {"rates": merged, "live": hit, "source": "BIS" if hit else "encoded"}

    live: Dict[str, float] = {}
    for area in _BIS_AREA:
        url = f"https://stats.bis.org/api/v1/data/WS_CBPOL_D/D.{area}/all"
        try:
            async with httpx.AsyncClient(timeout=_BIS_TIMEOUT, headers=_UA) as c:
                r = await c.get(url, params={"lastNObservations": 1, "format": "jsondata"})
            if r.status_code != 200 or "json" not in (r.headers.get("content-type") or ""):
                continue
            j = r.json()
            series = (((j.get("dataSets") or [{}])[0]).get("series") or {})
            for _, s in series.items():
                obs = (s.get("observations") or {})
                for _, val in obs.items():
                    if val and val[0] is not None:
                        live[area] = round(float(val[0]), 2)
                        break
                break
        except Exception:
            continue

    _put("bis", live)
    if apply:
        for a, v in live.items():
            POLICY_RATES[_BIS_AREA[a]] = v
    merged = dict(POLICY_RATES)
    return {"rates": merged, "live": live,
            "source": "BIS" if live else "encoded",
            "note": (f"{len(live)}/{len(_BIS_AREA)} banks live from BIS" if live
                     else "BIS unreachable — using encoded rates")}


def cb_divergence() -> dict:
    """Fed policy rate vs the peer (non-Fed) average — the central-bank divergence."""
    fed = POLICY_RATES.get("FED", 0.0)
    peers = [v for k, v in POLICY_RATES.items() if k != "FED"]
    avg = sum(peers) / len(peers) if peers else 0.0
    return {"fed": fed, "peer_avg": round(avg, 2), "fed_minus_peers": round(fed - avg, 2)}


async def free_macro() -> dict:
    """All free signals in one call, for the VAULTUM board. Every field degrades to None."""
    vix_closes = await yahoo_closes("%5EVIX", "1mo", "1d")     # ^VIX
    spx_closes = await yahoo_closes("%5EGSPC", "5d", "1d")     # ^GSPC
    qqq = await yahoo_closes("QQQ", "3mo", "1d")
    xlp = await yahoo_closes("XLP", "3mo", "1d")
    jpy = await yahoo_closes("JPY=X", "3mo", "1d")             # USD/JPY

    vix = vix_closes[-1] if vix_closes else None
    spx_chg = _roc(spx_closes, 1) if len(spx_closes) >= 2 else None
    # risk regime: QQQ 20d ROC minus XLP 20d ROC (risk-on if QQQ leads)
    qqq_roc, xlp_roc = _roc(qqq, 20), _roc(xlp, 20)
    regime_spread = (round(qqq_roc - xlp_roc, 2)
                     if (qqq_roc is not None and xlp_roc is not None) else None)
    # JPY carry/liquidity: USDJPY 30d ROC. Yen STRENGTHENING (USDJPY falling → ROC<0) =
    # carry unwind = risk-off = gold bid.
    jpy_roc = _roc(jpy, 30)
    fng = await fear_greed()
    geo = await geopolitical_risk()

    # derive a risk state for risk_appetite_score
    risk_state = None
    if any(x is not None for x in (spx_chg, vix, regime_spread, fng)):
        off = ((spx_chg is not None and spx_chg <= -0.8) or (vix is not None and vix >= 22)
               or (regime_spread is not None and regime_spread < -1.0)
               or (fng is not None and fng < 40))
        on = ((spx_chg is not None and spx_chg >= 0.6) and (vix is None or vix < 18)
              and (fng is None or fng > 55))
        risk_state = "risk_off" if off else "risk_on" if on else "neutral"

    return {
        "source": "free (Yahoo + alternative.me + GDELT)",
        "vix": vix, "spx_change_pct": spx_chg,
        "risk_regime_spread": regime_spread, "qqq_roc": qqq_roc, "xlp_roc": xlp_roc,
        "jpy_usd_roc_30d": jpy_roc, "fear_greed": fng, "geopolitical_risk": geo,
        "cb_divergence": cb_divergence(),
        "risk_state": risk_state,
    }


async def free_feeds_diagnostic() -> dict:
    """Which free feeds resolve right now (so you can verify they're live, no key needed)."""
    m = await free_macro()
    live = {k: (m.get(k) is not None) for k in
            ("vix", "spx_change_pct", "risk_regime_spread", "jpy_usd_roc_30d",
             "fear_greed", "geopolitical_risk")}
    return {"values": m, "live": live,
            "all_live": all(live.values()),
            "note": "free/no-key sources — no TwelveData quota used"}
