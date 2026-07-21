"""Weekly trade budget — soft per-tier caps that keep deployment on cadence (pure).

The operator's anticipated weekly cadence, encoded so STRATOPS won't over-deploy and
the paper run stays realistic:

    swing        1   (≈3-4 / month, price-action driven)
    intraday     5   (New York distribution)
    intrasession 5   (Asian accumulation)
    crt         10   (the 8h 1-5-9 strikes, Asian + NY)
    sniper       5   (OB-zone layered limits)
    prelondon    5   (pre-London CBDR limits)
    sd_fade      3   (seek-&-destroy extremes)

Focus is Asian + New York — ~10 taken trades across the 15 weekly sessions. A tier
at its cap is stood down (soft cap: it blocks NEW deploys, never touches open
positions). Unknown tiers are uncapped. Caps are per ISO week (Mon 00:00 UTC)."""

from __future__ import annotations

import datetime as _dt
from typing import Dict, Optional, Sequence

WEEKLY_BUDGET: Dict[str, int] = {
    "swing": 1,
    "intraday": 5,
    "intrasession": 5,
    "crt": 10,
    "sniper": 5,
    "prelondon": 5,
    "sd_fade": 3,
}


def week_start(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    """Monday 00:00 UTC of the current ISO week."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    monday = now - _dt.timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def count_by_tier(positions: Sequence[dict], since: _dt.datetime) -> Dict[str, int]:
    """Count positions opened at/after ``since``, grouped by trade_type."""
    counts: Dict[str, int] = {}
    for p in positions:
        ts = p.get("opened_at")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        if ts >= since:
            tt = p.get("trade_type") or "swing"
            counts[tt] = counts.get(tt, 0) + 1
    return counts


def within_budget(trade_type: str, used: int) -> bool:
    """Is there room this week for another ``trade_type``? (uncapped tiers = True)."""
    cap = WEEKLY_BUDGET.get(trade_type)
    return cap is None or used < cap


def budget_gate(trade_type: str, used: int) -> dict:
    """The soft-cap decision for one more trade of ``trade_type``."""
    cap = WEEKLY_BUDGET.get(trade_type)
    if cap is None:
        return {"ok": True, "used": used, "cap": None, "reason": "tier uncapped"}
    ok = used < cap
    return {"ok": ok, "used": used, "cap": cap, "room": max(0, cap - used),
            "reason": (f"{used}/{cap} used — room" if ok
                       else f"weekly {trade_type} cap reached ({used}/{cap})")}


def budget_status(counts: Dict[str, int]) -> dict:
    """Full weekly budget board from this week's per-tier counts."""
    board = {}
    for tier, cap in WEEKLY_BUDGET.items():
        used = counts.get(tier, 0)
        board[tier] = {"used": used, "cap": cap, "room": max(0, cap - used),
                       "over": used >= cap}
    total_used = sum(counts.values())
    return {"week_start": week_start().isoformat(), "by_tier": board,
            "total_taken": total_used,
            "note": "soft caps — blocks new deploys per tier, never closes open trades"}
