"""Case library — how each level ROLE has actually behaved, recent cases weighted most.

Two sources feed it:
  data/cases.csv   verified turns read from the owner's / journal charts (seed + history)
  live_cases       every sniper fire, auto-resolved: held = TP1 before the stop,
                   broke = stop first (services.sniper.resolve_pending)

score() returns per role: n, held, broke, Laplace hit-rate and a recency-weighted rate
(half-life CASE_HALF_LIFE_DAYS, default 180) — the live system adapts to what works NOW.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from typing import Dict, Iterable, List, Optional

from decouple import config

CASES_CSV = str(config("CASES_CSV", default="data/cases.csv"))
HALF_LIFE = float(config("CASE_HALF_LIFE_DAYS", default="180"))


def load_csv(path: str = CASES_CSV) -> List[Dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"date": dt.date.fromisoformat(r["date"]), "symbol": r["symbol"],
                            "role": r["role"].strip(), "outcome": r["outcome"].strip(),
                            "source": r.get("source") or "csv"})
            except (KeyError, ValueError):
                continue
    return out


def score(cases: Iterable[Dict], today: Optional[dt.date] = None, half_life: float = HALF_LIFE,
          symbol: Optional[str] = None) -> Dict[str, Dict]:
    today = today or dt.date.today()
    agg: Dict[str, Dict] = {}
    for c in cases:
        if c["outcome"] not in ("held", "broke"):
            continue
        if symbol and c["symbol"].replace("/", "").upper() != symbol.replace("/", "").upper():
            continue
        w = 0.5 ** (max(0, (today - c["date"]).days) / half_life)
        a = agg.setdefault(c["role"], {"n": 0, "held": 0, "broke": 0, "w_held": 0.0, "w": 0.0})
        a["n"] += 1
        a["held" if c["outcome"] == "held" else "broke"] += 1
        a["w"] += w
        a["w_held"] += w if c["outcome"] == "held" else 0.0
    out = {}
    for role, a in agg.items():
        out[role] = {"n": a["n"], "held": a["held"], "broke": a["broke"],
                     "rate": round((a["held"] + 1) / (a["n"] + 2), 2),
                     "recent_rate": round((a["w_held"] + 0.5) / (a["w"] + 1), 2)}
    return out


async def live_cases(limit: int = 500) -> List[Dict]:
    try:
        from sqlalchemy import select
        from database.db import AsyncSessionLocal
        from models.case_model import LiveCase
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(LiveCase).order_by(LiveCase.id.desc()).limit(limit))
                    ).scalars().all()
    except Exception:
        return []
    import json
    out = []
    for r in rows:
        try:
            votes = json.loads(r.council) if getattr(r, "council", None) else {}
        except ValueError:
            votes = {}
        for role in (r.roles or "").split("|"):
            if role:
                out.append({"date": (r.created_at or dt.datetime.now(dt.timezone.utc)).date(),
                            "symbol": r.symbol, "role": role, "outcome": r.status or "pending",
                            "source": "live", "votes": votes, "case_id": r.id})
    return out


async def all_cases() -> List[Dict]:
    return load_csv() + await live_cases()


def best_role(stats: Dict[str, Dict], roles: List[str]) -> Optional[Dict]:
    seen = [dict(stats[r], role=r) for r in roles if r in stats]
    return max(seen, key=lambda s: (s["recent_rate"], s["n"])) if seen else None
