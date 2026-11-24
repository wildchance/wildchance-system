"""Weekly DATA AUDIT — is every options / CME / retail / calendar input current and sane?

Checks (each finding: ✅ ok · ⚠️ stale or thin · ❌ wrong):
  options_strikes.csv  per symbol: is there a future expiry, how old is the screenshot, are the
                       strikes ordered (10Δ put < 25Δ put < pin < 25Δ call < 10Δ call), are they
                       within ±15 % of the live price, how many rows were interpolated / garbled
  CME chains           gold + each FX pair: bulletin age, strike count, put/call ratio present,
                       front future within 3 % of the live price
  retail levels        Myfxbook paste age
  calendar             last successful refresh
  data sources         any feed failing (health score < 0.5)
/ops/data-audit returns it; every Saturday 09:30 UTC it goes privately to the owner.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional

from decouple import config

STALE_DAYS = int(config("DATA_STALE_DAYS", default="7"))
STRIKES_CSV = str(config("OPTIONS_STRIKES_CSV", default="data/options_strikes.csv"))
RETAIL_CSV = str(config("RETAIL_LEVELS_CSV", default="data/retail_levels.csv"))
_SHOT = re.compile(r"(\d{4}-\d{2}-\d{2})")
_ATM = re.compile(r"ATM ([\d.]+)")
_IV = re.compile(r"IV ([\d.]+)%")


def max_distance(r: Dict, today: dt.date) -> float:
    """How far a strike may sit from spot: 3 × the expiry's implied move (min 8 %), else 15 %."""
    m = _IV.search(r.get("source") or "")
    if not m:
        return 0.15
    days = max(1, (dt.date.fromisoformat(r["expiry"]) - today).days)
    return max(0.08, 3 * float(m.group(1)) / 100 * (days / 365) ** 0.5)


def _f(x) -> Optional[float]:
    try:
        return float(x) if str(x).strip() else None
    except ValueError:
        return None


def audit_strikes(today: dt.date, live: Dict[str, float], path: str = STRIKES_CSV) -> List[Dict]:
    rows: Dict[str, List[Dict]] = defaultdict(list)
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                rows[r["symbol"].upper()].append(r)
    out = []
    for sym, rs in sorted(rows.items()):
        problems, notes = [], []
        future = [r for r in rs if dt.date.fromisoformat(r["expiry"]) >= today]
        shots = [dt.date.fromisoformat(m.group(1)) for r in rs if (m := _SHOT.search(r.get("source") or ""))]
        age = (today - max(shots)).days if shots else None
        if not future:
            problems.append("every expiry has passed — send new screens")
        elif age is not None and age > STALE_DAYS:
            problems.append(f"screens are {age} days old")
        nearest = min(future, key=lambda r: r["expiry"]) if future else None
        if nearest and dt.date.fromisoformat(nearest["expiry"]) > today + dt.timedelta(days=8):
            notes.append("no expiry within the next week (overnight / 1W screens missing)")
        for r in rs:
            p10, p25, c25, c10 = (_f(r.get(k)) for k in ("put10", "put25", "call25", "call10"))
            m = _ATM.search(r.get("source") or "")
            atm = float(m.group(1)) if m else None
            seq = [x for x in (p10, p25, atm, c25, c10) if x is not None]
            if seq != sorted(seq):
                problems.append(f"{r['expiry']}: strikes out of order {seq}")
            px = live.get(sym)
            lim = max_distance(r, today)
            if px and any(abs(x / px - 1) > lim for x in seq):
                problems.append(f"{r['expiry']}: a strike is > {lim:.0%} from the live price {px} (3× its implied move)")
        interp = sum(1 for r in rs if "interpolated" in (r.get("source") or ""))
        cropped = sum(1 for r in rs if "cropped" in (r.get("source") or "") or "skipped" in (r.get("source") or ""))
        if interp:
            notes.append(f"{interp} expiries interpolated (off-grid deltas)")
        if cropped:
            notes.append(f"{cropped} screens cropped / garbled")
        status = "❌" if any("order" in p or "live price" in p for p in problems) else "⚠️" if problems or notes else "✅"
        out.append({"source": "vol screens", "symbol": sym, "status": status, "age_days": age,
                    "expiries_ahead": len(future), "problems": problems, "notes": notes})
    return out


async def audit_cme(today: dt.date, live: Dict[str, float]) -> List[Dict]:
    from services import cme_bulletin as cb
    out = []
    chains = [("XAUUSD", await cb.cached_chain(max_age_days=365))]
    for p in ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NAS100", "US30", "SPX500"):
        chains.append((p, await cb.cached_fx_chain(p, max_age_days=365)))
    for sym, c in chains:
        if not c:
            out.append({"source": "CME bulletin", "symbol": sym, "status": "❌", "problems": ["no chain"]})
            continue
        age = (today - dt.date.fromisoformat(c["asof"])).days
        problems = []
        if age > 3:
            problems.append(f"bulletin {age} days old — upload Friday's sections")
        if len(c.get("strikes") or {}) < 15:
            problems.append(f"only {len(c.get('strikes') or {})} strikes")
        if c.get("put_call_ratio") is None:
            problems.append("no put/call ratio")
        px = live.get(sym)
        if px and c.get("spot") and abs(c["spot"] / px - 1) > 0.03:
            problems.append(f"front future {c['spot']:.5g} is > 3 % from live {px} — wrong contract or stale")
        out.append({"source": "CME bulletin", "symbol": sym, "age_days": age,
                    "status": "❌" if any("3 %" in p for p in problems) else "⚠️" if problems else "✅",
                    "problems": problems})
    return out


def audit_retail(today: dt.date, path: str = RETAIL_CSV) -> Dict:
    if not os.path.exists(path):
        return {"source": "retail averages", "status": "⚠️", "problems": ["no Myfxbook paste yet"]}
    with open(path, newline="") as f:
        dates = [r.get("date") for r in csv.DictReader(f) if r.get("date")]
    if not dates:
        return {"source": "retail averages", "status": "⚠️", "problems": ["no dated rows"]}
    age = (today - dt.date.fromisoformat(max(dates))).days
    return {"source": "retail averages", "age_days": age, "symbols": len(dates),
            "status": "⚠️" if age > STALE_DAYS else "✅",
            "problems": [f"Myfxbook paste is {age} days old"] if age > STALE_DAYS else []}


async def run(live: Optional[Dict[str, float]] = None) -> Dict:
    from services import data_feeds as df
    from services import econ_calendar as ec
    today = dt.date.today()
    live = live or {}
    if not live:
        try:
            from services.ohlc_service import fetch_ohlc_raw
            for s in ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "XAU/USD", "GBP/JPY", "EUR/JPY"):
                b = await fetch_ohlc_raw(s, "1h", 2)
                if b:
                    live[s.replace("/", "")] = b[-1]["close"]
        except Exception:
            pass
    items = audit_strikes(today, live) + await audit_cme(today, live) + [audit_retail(today)]
    try:
        await ec.ensure_loaded()
        cs = ec.status()
        h = cs.get("last_ok_h")
        items.append({"source": "calendar", "status": "✅" if h is not None and h <= 24 else "⚠️",
                      "problems": [] if h is not None and h <= 24 else ["no successful refresh in 24 h"]})
    except Exception:
        pass
    try:
        await df.ensure_loaded()
        bad = [s for s, h in df.status()["sources"].items() if h.get("score", 1) < 0.5]
        items.append({"source": "data feeds", "status": "⚠️" if bad else "✅",
                      "problems": [f"failing: {', '.join(sorted(bad))}"] if bad else []})
    except Exception:
        pass
    n = {k: sum(1 for i in items if i["status"] == k) for k in ("✅", "⚠️", "❌")}
    rep = {"as_of": today.isoformat(), "summary": f"✅ {n['✅']} · ⚠️ {n['⚠️']} · ❌ {n['❌']}", "items": items}
    try:
        from services import data_store
        await data_store.put("data_audit_last", rep)          # the terminal reads the last run
    except Exception:
        pass
    return rep


def message(rep: Dict) -> str:
    lines = [f"🧾 Weekly data audit — {rep['as_of']}  ({rep['summary']})"]
    for i in rep["items"]:
        if i["status"] == "✅":
            continue
        what = "; ".join(i.get("problems", []) + i.get("notes", []))
        lines.append(f"{i['status']} {i['source']}{' ' + i['symbol'] if i.get('symbol') else ''}: {what}")
    if len(lines) == 1:
        lines.append("Everything current and sane. 👍")
    return "\n".join(lines)
