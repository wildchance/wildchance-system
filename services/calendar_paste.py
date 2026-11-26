"""Calendar by paste — when Forex Factory rate-limits Render, the owner pastes the FF week.

The copied FF page loses its impact colours, so high impact is decided by the release NAME
(CPI, NFP, GDP, PCE, rate decisions / statements / press conferences, retail sales, ISM, jobs …).
Times are in the owner's FF timezone (CAL_PASTE_UTC_OFFSET, default +3 → converted to UTC).
Format handled (the FF copy-paste the owner sends):
  Tue / Nov 24 / 6:00pm / USD / CB Consumer Confidence / 92.4  91.7 / AUD / CPI y/y …
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Dict, List, Optional

from decouple import config

OFFSET_H = float(config("CAL_PASTE_UTC_OFFSET", default="3"))
CCYS = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "ALL"}
MONTHS = {m: i + 1 for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                          "Nov", "Dec"))}
HIGH = re.compile(r"\b(CPI|Non-Farm|NFP|Unemployment Rate|Employment Change|GDP|PCE|Retail Sales|ISM|JOLTS|"
                  r"Average Hourly Earnings|Cash Rate|Official Bank Rate|Policy Rate|Overnight Rate|Federal Funds|"
                  r"Main Refinancing|Interest Rate|Rate Statement|Monetary Policy|FOMC|Press Conference|"
                  r"Flash Manufacturing PMI|Flash Services PMI)\b", re.I)
_DAY = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$")
_DATE = re.compile(r"^([A-Z][a-z]{2}) (\d{1,2})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(am|pm)$|^(All Day|Tentative|Day \d)$", re.I)


_VAL = re.compile(r"^<?-?[\d.]+[%KMBT]?$|^\d+-\d+-\d+$")


def _tab_cols(row: str, nxt: str = "") -> Optional[List[str]]:
    """FF's copied value row keeps its tab columns: '\t-0.4%\t-0.2%\t1.0%\t' (actual, forecast,
    previous) or '\t\t3.0%\t2.6%\t' (not printed yet). A revised previous is pushed to its own
    line: '\t6.7K\t29.4K\t' then '1.3K'."""
    if "\t" not in row:
        return None
    parts = [p.strip() for p in row.split("\t")]
    if parts and parts[0] == "":
        parts = parts[1:]
    while parts and parts[-1] == "":
        parts.pop()
    if not parts or not all(p == "" or _VAL.match(p) for p in parts) or len(parts) > 3:
        return None
    parts += [""] * (3 - len(parts))
    if parts[2] == "" and _VAL.match(nxt or "") and parts[0] != "":
        parts[2] = nxt
    return parts


def looks_like_ff(text: str) -> bool:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return sum(1 for l in lines if _TIME.match(l)) >= 3 and sum(1 for l in lines if l.split()[0] in CCYS) >= 3


def parse(text: str, today: Optional[dt.date] = None, offset_h: float = OFFSET_H) -> List[Dict]:
    today = today or dt.date.today()
    out, day, tm, ccy = [], None, None, None
    raw = text.splitlines()
    lines = [l.strip() for l in raw]
    for i, l in enumerate(lines):
        if not l or _DAY.match(l):
            continue
        m = _DATE.match(l)
        if m and m.group(1) in MONTHS:
            y = today.year + (1 if MONTHS[m.group(1)] < today.month - 6 else 0)
            day, tm = dt.date(y, MONTHS[m.group(1)], int(m.group(2))), None
            continue
        t = _TIME.match(l)
        if t:
            tm = (int(t.group(1)) % 12 + (12 if t.group(3).lower() == "pm" else 0), int(t.group(2))) if t.group(1) else None
            continue
        first = l.split()[0]
        if first in CCYS and day:
            ccy = first
            title = next((x for x in lines[i + 1:i + 3] if x and x.split()[0] not in CCYS and not _TIME.match(x)), "")
            if not title or title.startswith(("Date", "Currency")):
                continue
            if tm is None:
                when = dt.datetime.combine(day, dt.time(0, 0))
            else:
                when = dt.datetime.combine(day, dt.time(*tm))
            when = (when - dt.timedelta(hours=offset_h)).replace(tzinfo=dt.timezone.utc)
            vals = lines[i + 2] if i + 2 < len(lines) else ""
            nums = re.findall(r"<?-?[\d.]+[%KMBT]?", vals) if not HIGH.search(vals) else []
            cols = _tab_cols(raw[i + 2] if i + 2 < len(raw) else "", lines[i + 3] if i + 3 < len(lines) else "")
            if cols:                                         # tab layout: actual · forecast · previous by position
                nums = None
            out.append({"title": title, "ccy": ccy if ccy != "ALL" else "USD",
                        "time_utc": when.isoformat(timespec="minutes"),
                        "impact": "High" if HIGH.search(title) and "Holiday" not in title and "Speaks" not in title
                        else "Medium",
                        # FF columns: actual · forecast · previous — three numbers means it has printed
                        "actual": cols[0] if cols else (nums[0] if len(nums) >= 3 else ""),
                        "forecast": cols[1] if cols else ((nums[1] if len(nums) >= 3 else nums[0]) if nums else ""),
                        "previous": cols[2] if cols else ((nums[2] if len(nums) >= 3 else nums[1]) if len(nums) > 1 else ""),
                        "source": "owner paste"})
    return out


async def store(events: List[Dict]) -> Dict:
    from services import data_store, econ_calendar as ec
    await ec.ensure_loaded()
    ec._events = ec.merge(ec._events, events)
    await data_store.put("econ_calendar", ec._events)
    return {"added": len(events), "high": sum(1 for e in events if e["impact"] == "High"), "stored": len(ec._events)}
