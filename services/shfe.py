"""Shanghai Futures Exchange daily settlement (gold, silver, copper) → China signals.

  - Shanghai gold premium: SHFE gold (CNY / gram) converted to USD / oz with USD/CNH,
    minus spot gold. A rising premium = strong Chinese physical demand (supports dips);
    a discount = China selling / weak demand.
  - Copper (CNY / tonne) day change: China growth pulse, leans on AUD and NZD.

Source: the exchange's public daily file kx{YYYYMMDD}.dat (JSON, published after the
15:00 Beijing close ≈ 07:00 UTC). EXPERIMENTAL: if Render cannot reach shfe.com.cn the
checklist shows it red and nothing else depends on it.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from decouple import config

OZ_G = 31.1034768
URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{d}.dat"
ENABLED = str(config("SHFE_ENABLED", default="true")).lower() in ("1", "true", "yes", "on")
PRODUCTS = {"au": "gold", "ag": "silver", "cu": "copper"}


def _f(v) -> Optional[float]:
    try:
        x = float(str(v).strip())
        return x if x else None
    except (TypeError, ValueError):
        return None


def parse(data: Dict) -> Dict[str, Dict]:
    """Most-active contract (max open interest) per product."""
    best: Dict[str, Dict] = {}
    for r in (data or {}).get("o_curinstrument") or []:
        pid = str(r.get("PRODUCTID") or "").strip().lower().replace("_f", "")
        month = str(r.get("DELIVERYMONTH") or "").strip()
        if pid not in PRODUCTS or not month.isdigit():
            continue                                       # skips the 小计 / 总计 subtotal rows
        oi = _f(r.get("OPENINTEREST")) or 0
        settle = _f(r.get("SETTLEMENTPRICE")) or _f(r.get("CLOSEPRICE"))
        if not settle:
            continue
        if pid not in best or oi > best[pid]["oi"]:
            best[pid] = {"product": PRODUCTS[pid], "contract": f"{pid}{month}", "settle": settle,
                         "prev": _f(r.get("PRESETTLEMENTPRICE")), "oi": oi,
                         "oi_chg": _f(r.get("OPENINTERESTCHG")) or 0, "volume": _f(r.get("VOLUME")) or 0}
    return best


def gold_premium(au_cny_g: float, usdcnh: float, xauusd: float) -> Dict:
    usd_oz = au_cny_g * OZ_G / usdcnh
    prem = usd_oz - xauusd
    read = ("strong China demand (supports gold dips)" if prem >= 15 else
            "China discount — weak demand / selling" if prem <= -5 else "neutral")
    return {"shfe_usd_oz": round(usd_oz, 2), "premium_usd": round(prem, 2),
            "premium_pct": round(prem / xauusd * 100, 2), "read": read}


def day_change(row: Dict) -> Optional[float]:
    if row.get("prev"):
        return round((row["settle"] / row["prev"] - 1) * 100, 2)
    return None


async def fetch(day: Optional[dt.date] = None) -> Optional[Dict]:
    """Latest available trading day (walks back up to 5 days for weekends / holidays)."""
    from services.data_feeds import _get
    d = day or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    for _ in range(6):
        data = await _get("shfe", URL.format(d=d.strftime("%Y%m%d")),
                          headers={"Referer": "https://www.shfe.com.cn/"}, timeout=15)
        if isinstance(data, dict) and data.get("o_curinstrument"):
            return {"day": d.isoformat(), "rows": parse(data)}
        d -= dt.timedelta(days=1)
    return None


async def report() -> Dict:
    if not ENABLED:
        return {"ok": False, "reason": "SHFE_ENABLED=false"}
    got = await fetch()
    if not got or not got["rows"]:
        return {"ok": False, "reason": "shfe.com.cn not reachable from this server (or no file yet)"}
    out: Dict = {"ok": True, "day": got["day"], "contracts": got["rows"]}
    au = got["rows"].get("au")
    if au:
        from utils.price_fetcher import get_forex_price
        cnh, xau = await get_forex_price("USD/CNH"), await get_forex_price("XAU/USD")
        if cnh and xau:
            out["gold_premium"] = gold_premium(au["settle"], cnh, xau)
    cu = got["rows"].get("cu")
    if cu:
        out["copper_chg_pct"] = day_change(cu)
    try:
        from services import data_store
        await data_store.put("shfe_last", out)
    except Exception:
        pass
    return out


def lines(rep: Dict) -> List[str]:
    if not rep.get("ok"):
        return [f"🇨🇳 SHFE: {rep.get('reason')}"]
    out = [f"🇨🇳 SHFE {rep['day']}"]
    g = rep.get("gold_premium")
    if g:
        out.append(f"Gold premium {g['premium_usd']:+.1f} $/oz ({g['premium_pct']:+.2f}%) — {g['read']}")
    if rep.get("copper_chg_pct") is not None:
        out.append(f"Copper {rep['copper_chg_pct']:+.2f}% (China growth pulse → AUD/NZD)")
    return out
