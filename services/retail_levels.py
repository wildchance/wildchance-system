"""Retail AVERAGE ENTRY prices (Myfxbook community outlook) — where the crowd's money sits.

Owner-pasted outlook rows: symbol, short avg price (pips P/L), long avg price (pips P/L),
current price. Underwater crowds sit at their average: a rally to the trapped LONGS'
average lets them exit flat (supply = resistance); a drop to the trapped SHORTS' average
(demand = support). Used as zone sources by the sweeper ('retail avg').
"""

from __future__ import annotations

import csv
import os
import re
from typing import Dict, List

from decouple import config

CSV = str(config("RETAIL_LEVELS_CSV", default="data/retail_levels.csv"))
_ROW = re.compile(r"([A-Z]{6})\s+([\d.,]+)\s+([+-]\d+) pips\s+([\d.,]+)\s+([+-]\d+) pips\s+([\d.,]+)")


def parse(text: str) -> List[Dict]:
    out = []
    for m in _ROW.finditer(text):
        sym = m.group(1)
        short_avg, long_avg, cur = (float(m.group(i).replace(",", "")) for i in (2, 4, 6))
        if not (short_avg and long_avg and cur):
            continue
        if not (0.8 < short_avg / cur < 1.25 and 0.8 < long_avg / cur < 1.25):
            continue                                       # broken / stale rows
        out.append({"symbol": sym, "short_avg": short_avg, "short_pips": int(m.group(3)),
                    "long_avg": long_avg, "long_pips": int(m.group(5)), "price": cur})
    return out


def load(path: str = CSV) -> Dict[str, Dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["symbol"]: {k: (float(v) if k not in ("symbol", "date") else v) for k, v in r.items()}
                for r in csv.DictReader(f)}


def levels(symbol: str, path: str = CSV) -> List[Dict]:
    r = load(path).get(symbol.upper().replace("/", ""))
    if not r:
        return []
    out = []
    if r["long_pips"] < 0:
        out.append({"price": r["long_avg"], "label": "retail longs' avg (trapped → supply)"})
    if r["short_pips"] < 0:
        out.append({"price": r["short_avg"], "label": "retail shorts' avg (trapped → demand)"})
    return out
