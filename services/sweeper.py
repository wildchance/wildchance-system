"""SWEEPER drones — multi-asset recon that finds where independent maps agree.

Every sweep flies the whole universe (FX majors + minors, indices, commodities, crypto,
stocks) and, per asset, overlays:

  daily CBDR    today's / the last completed 14:00–20:00 NY box → SD ladder (±0.5…±4)
  weekly CBDR   Monday's box → SD ladder (the week's map)
  yearly        golden clusters from the yearly / anchor / event boxes (cached 6h)
  options       dealer walls / flip / max pain (CBOE ETF chains, Deribit for crypto)

A ZONE is any price where ≥ 2 different maps sit within the asset's tolerance. Zones
near price are scored (sources × closeness) and reported; the hottest assets join the
sniper's next scan, so signals move with the daily + weekly CBDR across many markets.
One hourly-bars call per asset per sweep (the daily + weekly boxes share it).
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List, Optional

from decouple import config

UNIVERSE = str(config("SWEEP_UNIVERSE", default=(
    "EUR/USD,GBP/USD,USD/JPY,USD/CHF,USD/CAD,AUD/USD,NZD/USD,"
    "EUR/JPY,GBP/JPY,EUR/GBP,AUD/JPY,EUR/CHF,"
    "XAU/USD,XAG/USD,BTC/USD,ETH/USD,NAS100,US30,NVDA")))
NEAR_PCT = float(config("SWEEP_NEAR_PCT", default="1.5"))     # report zones within this % of price
HOT_SCORE = float(config("SWEEP_HOT_SCORE", default="3"))

_last: Dict = {"t": 0.0, "report": None}


def last_complete_box(by_day: Dict[dt.date, tuple], now_ny: dt.datetime) -> Optional[tuple]:
    """Today's CBDR once it has closed (≥ 20:00 NY), else the latest earlier day's."""
    days = sorted(d for d in by_day if d < now_ny.date() or (d == now_ny.date() and now_ny.hour >= 20))
    return by_day[days[-1]] if days else None


def ladder_levels(box: Optional[tuple], tag: str) -> List[Dict]:
    if not box or box[1] <= box[0]:
        return []
    lo, hi = box
    rng = hi - lo
    out = []
    k = -4.0
    while k <= 4.0 + 1e-9:
        out.append({"price": hi + k * rng, "src": tag, "label": f"{tag} {k:+g}"})
        k = round(k + 0.5, 3)
    return out


def zones(levels: List[Dict], tol: float, price: float, near_pct: float = NEAR_PCT) -> List[Dict]:
    """Group levels from ≥2 DIFFERENT sources within tol; keep those near price."""
    lv = sorted((x for x in levels if x.get("price")), key=lambda x: x["price"])
    out, cur = [], []

    def flush():
        srcs = {x["src"] for x in cur}
        if len(srcs) >= 2:
            mid = sum(x["price"] for x in cur) / len(cur)
            dist = 100 * (mid - price) / price
            if abs(dist) <= near_pct:
                out.append({"low": min(x["price"] for x in cur), "high": max(x["price"] for x in cur), "mid": mid,
                            "sources": sorted(srcs), "members": [x["label"] for x in cur][:6],
                            "distance_pct": round(dist, 2),
                            "score": round(len(srcs) + max(0.0, 1 - abs(dist) / near_pct), 2)})
    for x in lv:
        if cur and (x["price"] - cur[-1]["price"] > tol or x["price"] - cur[0]["price"] > 1.5 * tol):
            flush()
            cur = []
        cur.append(x)
    if cur:
        flush()
    return sorted(out, key=lambda z: -z["score"])


async def sweep_one(symbol: str) -> Dict:
    from services import options_levels as ol
    from services import yearly_cbdr as yc
    from services.auto_boxes import cbdr_by_day
    from services.ohlc_service import fetch_ohlc_raw
    try:
        bars = await fetch_ohlc_raw(symbol, "1h", 140, timezone="America/New_York")
    except Exception:
        bars = []
    if not bars:
        return {"symbol": symbol, "ok": False, "reason": "no hourly data"}
    price = bars[-1]["close"]
    by_day = cbdr_by_day(bars)
    now_ny = dt.datetime.fromisoformat(str(bars[-1]["time"])[:19])
    daily_box = last_complete_box(by_day, now_ny)
    today = now_ny.date()
    monday = today - dt.timedelta(days=today.weekday())
    levels = ladder_levels(daily_box, "daily CBDR") + ladder_levels(by_day.get(monday), "weekly CBDR")
    try:
        r = await yc.run(symbol)
        for c in (r.get("clusters") or []) if r.get("ok") else []:
            levels.append({"price": c["mid"], "src": "yearly", "label": "yearly " + ", ".join(c["members"][:2])})
    except Exception:
        pass
    try:
        o = await ol.run(symbol, price)
        for x in o.get("levels") or []:
            levels.append({"price": x["price"], "src": "options", "label": x["label"]})
    except Exception:
        pass
    try:
        from services import owner_plans
        for p in owner_plans.active(symbol, price):
            levels.append({"price": (p["low"] + p["high"]) / 2, "src": "owner plan", "label": f"PLAN {p['side']}"})
    except Exception:
        pass
    tol = yc.tolerance(symbol, price)
    zs = zones(levels, tol, price)
    return {"symbol": symbol, "ok": True, "price": price, "zones": zs[:4],
            "best": zs[0] if zs else None, "maps": sorted({x["src"] for x in levels})}


async def sweep(universe: Optional[List[str]] = None, notify: bool = False) -> Dict:
    syms = universe or [s.strip() for s in UNIVERSE.split(",") if s.strip()]
    results = []
    for s in syms:
        try:
            results.append(await sweep_one(s))
        except Exception as e:
            results.append({"symbol": s, "ok": False, "reason": f"{type(e).__name__}"})
    ranked = sorted((r for r in results if r.get("best")), key=lambda r: -r["best"]["score"])
    report = {"as_of_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
              "assets": len(syms), "with_zones": len(ranked),
              "top": [{"symbol": r["symbol"], "price": r["price"], **r["best"]} for r in ranked[:10]],
              "results": results}
    _last.update(t=time.time(), report=report)
    if notify and ranked:
        from services.realtime import alert_once
        await alert_once(f"sweep:{report['as_of_utc'][:13]}", card(report), ttl_hours=5)
    return report


def hot_symbols(max_age_h: float = 7.0, n: int = 6) -> List[str]:
    """Assets whose best zone is strong (≥ HOT_SCORE) — added to the sniper's scan."""
    rep = _last.get("report")
    if not rep or time.time() - _last["t"] > max_age_h * 3600:
        return []
    return [t["symbol"] for t in rep["top"] if t["score"] >= HOT_SCORE][:n]


def card(rep: Dict) -> str:
    from routes.cbdr import px
    lines = [f"🛸 *Sweeper drones* — {rep['with_zones']}/{rep['assets']} markets with confluence near price"]
    for t in rep["top"][:8]:
        arrow = "▲" if t["distance_pct"] > 0 else "▼"
        lines.append(f"{arrow} *{t['symbol']}* {px(t['symbol'], t['low'])}–{px(t['symbol'], t['high'])} "
                     f"({t['distance_pct']:+.2f}%) · {' + '.join(t['sources'])}")
    lines.append("_Hot markets join the sniper's next scan — it still needs a trigger + the council._")
    return "\n".join(lines)
