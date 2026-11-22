"""Options-expiry calendar — deterministic, no network (the flowchart's EXPIRY TABLE).

Big expiries pin price near large open-interest strikes into the event and release
volatility after it, so mean-reversion fades into an expiry and breakouts after it
behave differently. Rules (exchange holidays NOT applied — a holiday moves the date
one business day earlier; check the exchange calendar around holidays):

  US equity/ETF monthly   3rd Friday of the month (quarterly = Mar/Jun/Sep/Dec "triple witching")
  CME gold options (OG)   4th last business day of the month BEFORE the contract month
  CME FX options          2nd Friday before the 3rd Wednesday of the contract month
  Deribit BTC/ETH         every Friday 08:00 UTC; monthly = last Friday; quarterly = last Fri of Mar/Jun/Sep/Dec
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List

FX_ASSETS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
INDEX_ASSETS = ["US500", "NAS100", "US30"]


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def last_weekday(year: int, month: int, weekday: int) -> dt.date:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def equity_monthly(year: int, month: int) -> dt.date:
    return nth_weekday(year, month, 4, 3)


def cme_gold(contract_year: int, contract_month: int) -> dt.date:
    """4th last business day of the month before the contract month."""
    y, m = (contract_year - 1, 12) if contract_month == 1 else (contract_year, contract_month - 1)
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    count = 0
    while True:
        if d.weekday() < 5:
            count += 1
            if count == 4:
                return d
        d -= dt.timedelta(days=1)


def cme_fx(year: int, month: int) -> dt.date:
    return nth_weekday(year, month, 2, 3) - dt.timedelta(days=12)


def upcoming(start: dt.date, days: int = 35) -> List[Dict]:
    """All tracked expiries in [start, start+days], sorted."""
    end = start + dt.timedelta(days=days)
    out: List[Dict] = []
    y, m = start.year, start.month
    for _ in range(3):
        q = m in (3, 6, 9, 12)
        out.append({"date": equity_monthly(y, m), "market": "US equity/ETF options",
                    "type": "QUARTERLY (triple witching)" if q else "monthly",
                    "assets": INDEX_ASSETS, "major": q})
        out.append({"date": cme_fx(y, m), "market": "CME FX options", "type": "monthly",
                    "assets": FX_ASSETS, "major": q})
        ny, nm = (y + (m == 12), m % 12 + 1)
        out.append({"date": cme_gold(ny, nm), "market": "CME gold options (OG)",
                    "type": f"{dt.date(ny, nm, 1):%b} contract", "assets": ["XAUUSD"],
                    "major": True})
        lastf = last_weekday(y, m, 4)
        out.append({"date": lastf, "market": "Deribit BTC/ETH options",
                    "type": "QUARTERLY" if q else "monthly", "assets": ["BTCUSD"],
                    "major": True, "time_utc": "08:00"})
        y, m = ny, nm
    d = start
    while d <= end:                                       # Deribit weeklies
        if d.weekday() == 4 and d != last_weekday(d.year, d.month, 4):
            out.append({"date": d, "market": "Deribit BTC/ETH options", "type": "weekly",
                        "assets": ["BTCUSD"], "major": False, "time_utc": "08:00"})
        d += dt.timedelta(days=1)
    seen, res = set(), []
    for e in sorted(out, key=lambda e: (e["date"], e["market"])):
        key = (e["date"], e["market"])
        if start <= e["date"] <= end and key not in seen:
            seen.add(key)
            res.append({**e, "date": e["date"].isoformat(),
                        "days_away": (e["date"] - start).days})
    return res


def flags_for(symbol: str, on: dt.date, window: int = 1) -> List[Dict]:
    """Major expiries for this asset within ±window days → [EXPIRY] flag."""
    s = symbol.upper().replace("/", "")
    return [e for e in upcoming(on - dt.timedelta(days=window), 2 * window)
            if e["major"] and s in e["assets"]]
