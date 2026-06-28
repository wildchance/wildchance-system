"""Yearly Quarterly-Theory CBDR — pure, deterministic, no I/O.

Applies ICT Quarterly Theory at the YEARLY scale...
"""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, List, Optional
from cbdr.engine import build_cbdr, read_bias, extension_read, lookback_control, CBDR

PHASES = {1: "accumulation", 2: "manipulation", 3: "distribution", 4: "continuation_or_reversal"}


def quarter_of_month(month: int) -> int:
    return (month - 1) // 3 + 1


def phase_of_month(month: int) -> str:
    return PHASES[quarter_of_month(month)]


def q1_box_from_closes(closes: List[float]) -> CBDR:
    if not closes:
        raise ValueError("need at least one Q1 close to build the yearly box")
    return build_cbdr(max(closes), min(closes))


def yearly_boxes(rows: List[dict]) -> Dict[str, CBDR]:
    by_year: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        d = str(r.get("date", ""))
        if len(d) < 7:
            continue
        try:
            month = int(d[5:7])
            close = float(r["close"])
        except (ValueError, KeyError, TypeError):
            continue
        if month <= 3:
            by_year[d[:4]].append(close)
    return {y: q1_box_from_closes(cs) for y, cs in by_year.items() if cs}


def yearly_read(rows: List[dict], price: float, today_iso: str) -> dict:
    boxes = yearly_boxes(rows)
    year = today_iso[:4]
    month = int(today_iso[5:7])
    out: dict = {
        "year": year,
        "quarter": quarter_of_month(month),
        "phase": phase_of_month(month),
        "price": price,
    }
    cur: Optional[CBDR] = boxes.get(year)
    if cur is not None:
        out["box"] = cur.to_dict()
        out["bias"] = read_bias(price, cur)
        out["extension"] = extension_read(price, cur)
        out["q1_complete"] = quarter_of_month(month) > 1
    else:
        out["box"] = None
        out["note"] = f"no Q1 data yet for {year} — the accumulation range forms through March"
    out["lookback"] = lookback_control(boxes, price)
    return out
