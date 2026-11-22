"""Options open-interest maths per expiry: put/call walls, max pain, P/C ratio (pure).

Input rows: {"expiry": "2026-12-25", "strike": 100000, "type": "C"|"P", "oi": float}.
Max pain = settlement price at which option holders' total intrinsic value is lowest —
price tends to gravitate toward it into a large expiry (a pinning tendency, not a law).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional


def by_expiry(rows: List[Dict], spot: Optional[float] = None) -> List[Dict]:
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        if r.get("oi") and r.get("strike"):
            groups[r["expiry"]].append(r)
    out = []
    for exp in sorted(groups):
        g = groups[exp]
        calls = defaultdict(float)
        puts = defaultdict(float)
        for r in g:
            (calls if r["type"] == "C" else puts)[float(r["strike"])] += float(r["oi"])
        strikes = sorted(set(calls) | set(puts))
        pain = {s: sum(oi * max(0.0, s - k) for k, oi in calls.items()) +
                   sum(oi * max(0.0, k - s) for k, oi in puts.items()) for s in strikes}
        c_oi, p_oi = sum(calls.values()), sum(puts.values())
        item = {"expiry": exp, "call_oi": round(c_oi, 2), "put_oi": round(p_oi, 2),
                "put_call_ratio": round(p_oi / c_oi, 2) if c_oi else None,
                "max_pain": min(pain, key=pain.get) if pain else None,
                "call_wall": max(calls, key=calls.get) if calls else None,
                "put_wall": max(puts, key=puts.get) if puts else None}
        if spot and item["max_pain"]:
            item["max_pain_vs_spot_pct"] = round(100 * (item["max_pain"] - spot) / spot, 2)
        out.append(item)
    return out


def parse_deribit(instruments: List[Dict]) -> List[Dict]:
    """Deribit get_book_summary_by_currency(kind=option) → OI rows.
    instrument_name like 'BTC-27DEC26-100000-C'; open_interest in coins."""
    import datetime as dt
    rows = []
    for it in instruments:
        try:
            _, exp, strike, typ = it["instrument_name"].split("-")
            d = dt.datetime.strptime(exp, "%d%b%y").date().isoformat()
            rows.append({"expiry": d, "strike": float(strike), "type": typ,
                         "oi": float(it.get("open_interest") or 0)})
        except (KeyError, ValueError):
            continue
    return rows
