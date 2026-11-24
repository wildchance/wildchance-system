"""Owner plans — the trader's own zones, driven into the sniper and the council.

data/owner_plans.csv: symbol, side, zone_low, zone_high, invalidation, targets (';'),
valid_from, valid_until, label. A plan is ACTIVE between its dates and until price
closes beyond its invalidation. The sniper treats an active plan zone like a golden
cluster (fixed side, the owner's targets); the council's 'plan' agent votes +1 for a
trade that matches an active plan zone and −1 for one that contradicts it.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from typing import Dict, List, Optional

from decouple import config

PLANS_CSV = str(config("OWNER_PLANS_CSV", default="data/owner_plans.csv"))


def _s(x: str) -> str:
    return (x or "").upper().replace("/", "")


def load(path: Optional[str] = None) -> List[Dict]:
    path = path or PLANS_CSV
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                lo, hi = sorted((float(r["zone_low"]), float(r["zone_high"])))
                out.append({"symbol": r["symbol"], "side": r["side"].strip().upper(), "low": lo, "high": hi,
                            "invalidation": float(r["invalidation"]) if r.get("invalidation") else None,
                            "targets": [float(t) for t in (r.get("targets") or "").split(";") if t.strip()],
                            "valid_from": dt.date.fromisoformat(r["valid_from"]),
                            "valid_until": dt.date.fromisoformat(r["valid_until"]),
                            "label": r.get("label") or "owner plan"})
            except (KeyError, ValueError):
                continue
    return out


def active(symbol: str, price: Optional[float] = None, today: Optional[dt.date] = None,
           path: Optional[str] = None) -> List[Dict]:
    """Plans for ``symbol`` inside their dates and not invalidated by ``price``."""
    today = today or dt.date.today()
    out = []
    for p in load(path):
        if _s(p["symbol"]) != _s(symbol) or not (p["valid_from"] <= today <= p["valid_until"]):
            continue
        inv = p["invalidation"]
        if price is not None and inv is not None and (
                (p["side"] == "SELL" and price > inv) or (p["side"] == "BUY" and price < inv)):
            continue
        out.append(p)
    return out


def at_zone(plans: List[Dict], lo: float, hi: float, tol: float = 0.0) -> List[Dict]:
    return [p for p in plans if hi >= p["low"] - tol and lo <= p["high"] + tol]


def as_cluster(p: Dict) -> Dict:
    """A plan zone in the shape of a yearly-engine cluster (so the sniper can use it)."""
    return {"low": p["low"], "high": p["high"], "mid": (p["low"] + p["high"]) / 2,
            "members": [f"PLAN {p['side']}: {p['label']}"], "years": ["plan"], "plan": p,
            "in_ote": False, "ipda": [], "keys": []}
