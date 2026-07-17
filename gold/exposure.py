"""ARCENT logistics — gold exposure cap (pure).

Swing + intraday + intrasession + CRT + pre-London + S&D limits can all open on the
same side; without a cap the aggregate risk stacks. This bounds it: a ceiling on
total open $ risk and on the number of concurrent gold positions. STRATOPS reads
this to decide how many of its ranked candidates it can actually deploy.
"""

from __future__ import annotations

from typing import Sequence

DEFAULT_RISK_CAP_USD = 120.0     # max aggregate open $ risk across all gold trades
DEFAULT_MAX_POSITIONS = 6        # max concurrent gold positions (OPEN + PENDING)


def open_risk(positions: Sequence[dict]) -> float:
    """Sum of risk_usd across OPEN/PENDING positions."""
    return round(sum(float(p.get("risk_usd") or 0.0)
                     for p in positions
                     if p.get("status") in ("OPEN", "PENDING")), 2)


def open_count(positions: Sequence[dict]) -> int:
    return sum(1 for p in positions if p.get("status") in ("OPEN", "PENDING"))


def can_open(positions: Sequence[dict], new_risk: float,
             risk_cap: float = DEFAULT_RISK_CAP_USD,
             max_positions: int = DEFAULT_MAX_POSITIONS) -> dict:
    """Is there room to open a new position of ``new_risk``?"""
    used = open_risk(positions)
    n = open_count(positions)
    room = round(risk_cap - used, 2)
    if n >= max_positions:
        return {"ok": False, "reason": f"position cap reached ({n}/{max_positions})",
                "open_risk": used, "room": room, "count": n}
    if new_risk > room:
        return {"ok": False, "reason": f"risk cap: ${new_risk} > ${room} left of ${risk_cap}",
                "open_risk": used, "room": room, "count": n}
    return {"ok": True, "reason": f"room for ${new_risk} (${room} of ${risk_cap} free, {n}/{max_positions})",
            "open_risk": used, "room": room, "count": n}
