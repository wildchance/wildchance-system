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


def looks_like_ff(text: str) -> bool:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return sum(1 for l in lines if _TIME.match(l)) >= 3 and sum(1 for l in lines if l.split()[0] in CCYS) >= 3


def parse(text: str, today: Optional[dt.date] = None, offset_h: float = OFFSET_H) -> List[Dict]:
    today = today or dt.date.today()
    out, day, tm, ccy = [], None, None, None
    lines = [l.strip() for l in text.splitlines()]
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
            nums = re.findall(r"-?[\d.]+[%KMBT]?", vals) if not HIGH.search(vals) else []
            out.append({"title": title, "ccy": ccy if ccy != "ALL" else "USD",
                        "time_utc": when.isoformat(timespec="minutes"),
                        "impact": "High" if HIGH.search(title) and "Holiday" not in title and "Speaks" not in title
                        else "Medium",
                        # FF columns: actual · forecast · previous — three numbers means it has printed
                        "actual": nums[0] if len(nums) >= 3 else "",
                        "forecast": (nums[1] if len(nums) >= 3 else nums[0]) if nums else "",
                        "previous": (nums[2] if len(nums) >= 3 else nums[1]) if len(nums) > 1 else "",
                        "source": "owner paste"})
    return out


async def store(events: List[Dict]) -> Dict:
    from services import data_store, econ_calendar as ec
    await ec.ensure_loaded()
    ec._events = ec.merge(ec._events, events)
    await data_store.put("econ_calendar", ec._events)
    return {"added": len(events), "high": sum(1 for e in events if e["impact"] == "High"), "stored": len(ec._events)}
