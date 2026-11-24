"""Cycles — a first, rough read (≈19 years of daily data is only 4–5 US terms).

PRESIDENTIAL  year-in-term of the US presidential cycle (2009 = 1 post-election,
              2010 = 2 midterm, 2011 = 3 pre-election, 2012 = 4 election …): each
              asset's average calendar-year return and % of up-years per cycle year,
              plus where we are now. n per cycle year is small — read it as a lean.
HURST         nominal cycles (Hurst): 20, 40, 80 trading days, 20 weeks (~100d),
              40 weeks (~200d). For each: days since the last trough (lowest low in
              1.5× the cycle) → phase. ≥ 0.85 = a trough is due; > 1.25 = overdue
              (the cycle may be inverting / trend is overpowering it).
Bars: (date, o, h, l, c) oldest-first. Pure.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence, Tuple

Bar = Tuple[dt.date, float, float, float, float]
NAMES = {1: "post-election", 2: "midterm", 3: "pre-election", 4: "election"}
HURST = (("20d", 20), ("40d", 40), ("80d", 80), ("20w", 100), ("40w", 200))


def year_in_term(year: int) -> int:
    return (year - 2009) % 4 + 1


def presidential(bars: Sequence[Bar], today: Optional[dt.date] = None) -> Optional[Dict]:
    by_year: Dict[int, List[Bar]] = {}
    for b in bars:
        by_year.setdefault(b[0].year, []).append(b)
    today = today or (bars[-1][0] if bars else dt.date.today())
    rets: Dict[int, List[float]] = {1: [], 2: [], 3: [], 4: []}
    for y, bs in by_year.items():
        if y >= today.year or len(bs) < 200:                # complete years only
            continue
        first, last = bs[0][1], bs[-1][4]
        if first:
            rets[year_in_term(y)].append(100 * (last / first - 1))
    if not any(rets.values()):
        return None
    table = {}
    for k, xs in rets.items():
        if xs:
            table[k] = {"name": NAMES[k], "n": len(xs), "avg_pct": round(sum(xs) / len(xs), 1),
                        "pct_up": round(100 * sum(x > 0 for x in xs) / len(xs))}
    now = year_in_term(today.year)
    return {"now": {"year": today.year, "term_year": now, "name": NAMES[now], **(table.get(now) or {})},
            "by_term_year": table}


def midterm_windows(bars: Sequence[Bar], today: Optional[dt.date] = None) -> Optional[Dict]:
    """Return from the first close on/after Oct 1 of each midterm year to the last close
    on/before Jun 30 next year (Fisher's window) — checked on THIS market's own data."""
    today = today or (bars[-1][0] if bars else dt.date.today())
    rows = []
    for y in range(2002, today.year + 1, 4):
        start = next((b for b in bars if b[0] >= dt.date(y, 10, 1)), None)
        if not start or start[0] > dt.date(y, 10, 15):
            continue
        end_cut = min(dt.date(y + 1, 6, 30), today)
        end = [b for b in bars if start[0] < b[0] <= end_cut]
        if not end:
            continue
        rows.append({"midterm": y, "from": start[0].isoformat(), "to": end[-1][0].isoformat(),
                     "return_pct": round(100 * (end[-1][4] / start[4] - 1), 1),
                     "complete": end_cut == dt.date(y + 1, 6, 30)})
    done = [r for r in rows if r["complete"]]
    if not rows:
        return None
    return {"windows": rows, "completed": len(done),
            "pct_positive": round(100 * sum(r["return_pct"] > 0 for r in done) / len(done)) if done else None,
            "avg_return_pct": round(sum(r["return_pct"] for r in done) / len(done), 1) if done else None,
            "current": next((r for r in rows if not r["complete"]), None)}


def hurst(bars: Sequence[Bar]) -> List[Dict]:
    out = []
    for name, n in HURST:
        win = list(bars)[-int(n * 1.5):]
        if len(win) < n:
            continue
        i = min(range(len(win)), key=lambda j: win[j][3])
        since = len(win) - 1 - i
        phase = since / n
        state = ("overdue" if phase > 1.25 else "trough due" if phase >= 0.85
                 else "rising (early)" if phase < 0.5 else "mid / topping")
        out.append({"cycle": name, "days_since_trough": since, "phase": round(phase, 2),
                    "state": state, "trough": win[i][0].isoformat(), "low": win[i][3]})
    return out


def read(bars: Sequence[Bar], today: Optional[dt.date] = None) -> Dict:
    return {"presidential": presidential(bars, today), "midterm": midterm_windows(bars, today),
            "hurst": hurst(bars)}


def card_line(c: Optional[Dict]) -> Optional[str]:
    if not c:
        return None
    parts = []
    p = (c.get("presidential") or {}).get("now") or {}
    if p.get("n"):
        parts.append(f"🗳 {p['name']} year: avg {p['avg_pct']:+}% · up {p['pct_up']}% (n={p['n']})")
    m = c.get("midterm") or {}
    if m.get("completed"):
        cur = m.get("current")
        parts.append(f"🏛 midterm window Oct→Jun: {m['pct_positive']}% up, avg {m['avg_return_pct']:+}% "
                     f"(n={m['completed']})" + (f" · this one {cur['return_pct']:+}% so far" if cur else ""))
    due = [h["cycle"] for h in c.get("hurst") or [] if h["state"] == "trough due"]
    if due:
        parts.append("🌀 Hurst trough due: " + ", ".join(due))
    return " · ".join(parts) or None
