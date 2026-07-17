"""Persist and monitor tracked gold swing positions.

DB glue around the pure lifecycle core (gold.position):
  • open_from_signal — turn a fired signal card into an OPEN row (deduped so the
    same side isn't opened twice in one day);
  • monitor        — walk OPEN rows against the live price, trail to break-even
    after TP1, and close on target / stop / the weekly time-stop;
  • list_positions / open_count — read helpers for the routes.

Closed rows carry a realized ``result_r`` and are read by the gold scorecard.
"""

from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.gold_position_model import GoldPosition
from gold import position as pos
from gold.position import deadline_for
from gold.exposure import can_open


async def _live_positions(db: AsyncSession) -> List[dict]:
    """OPEN + PENDING positions as dicts (for the exposure cap)."""
    res = await db.execute(
        select(GoldPosition).where(GoldPosition.status.in_(("OPEN", "PENDING"))))
    return [_to_dict(r) for r in res.scalars().all()]


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


async def _open_same_side_today(db: AsyncSession, symbol: str, side: str,
                                trade_type: str) -> Optional[GoldPosition]:
    res = await db.execute(
        select(GoldPosition)
        .where(GoldPosition.status == "OPEN",
               GoldPosition.symbol == symbol,
               GoldPosition.side == side,
               GoldPosition.trade_type == trade_type)
        .order_by(GoldPosition.opened_at.desc()))
    row = res.scalars().first()
    if row is None or row.opened_at is None:
        return row
    opened = row.opened_at
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=_dt.timezone.utc)
    return row if opened.date() == _utcnow().date() else None


async def open_from_signal(db: AsyncSession, sig: dict,
                           source: str = "gold") -> Optional[dict]:
    """Persist a fired signal as an OPEN swing, or None if not tracked.

    Skips non-signals, gate-blocked cards, and duplicates (same side already open
    today). Idempotent enough for a cron to call on every scan.
    """
    if sig.get("signal") not in ("LONG", "SHORT"):
        return None
    if not sig.get("gate", {}).get("allow", False):
        return None
    entry, stop = sig.get("entry"), sig.get("stop")
    if entry is None or stop is None:
        return None

    side = "long" if sig["signal"] == "LONG" else "short"
    symbol = sig.get("instrument", "XAU/USD")
    trade_type = sig.get("trade_type") or "swing"
    # One position per (symbol, side, trade_type) per day — different tiers on the
    # same side (e.g. an intraday and a swing) are allowed to co-exist.
    dup = await _open_same_side_today(db, symbol, side, trade_type)
    if dup is not None:
        return {"skipped": "duplicate — same side/type already open today", "id": dup.id}

    cap = can_open(await _live_positions(db), float(sig.get("risk_usd") or 0.0))
    if not cap["ok"]:
        return {"skipped": f"exposure cap — {cap['reason']}", **cap}

    tps = [t.get("price") for t in sig.get("targets", [])]
    tps += [None] * (4 - len(tps))
    now = _utcnow()
    deadline = deadline_for(trade_type, now, sig.get("session_end_hour"))
    row = GoldPosition(
        symbol=symbol, side=side, trade_type=trade_type, deadline=deadline,
        entry=float(entry), stop=float(stop), stop_initial=float(stop),
        tp1=tps[0], tp2=tps[1], tp3=tps[2], tp4=tps[3],
        lot=sig.get("lot"), risk_usd=sig.get("risk_usd"),
        profile=sig.get("profile"), source=source,
        justification=(sig.get("justification") or "")[:480],
        action="BUY" if side == "long" else "SELL",
        status="OPEN",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


async def open_limit(db: AsyncSession, card: dict, source: str,
                     expires_at: Optional[_dt.datetime] = None) -> Optional[dict]:
    """Persist a sized limit card as a PENDING position (fills when price touches).

    Deduped per (symbol, side, trade_type) per day. The monitor activates it to
    OPEN on touch, or cancels it at ``expires_at``.
    """
    if card.get("signal") not in ("LONG", "SHORT"):
        return None
    if not card.get("gate", {}).get("allow", False):
        return {"skipped": "gate blocked", "reason": card.get("gate", {}).get("reason")}
    entry, stop = card.get("entry"), card.get("stop")
    if entry is None or stop is None:
        return None
    side = "long" if card["signal"] == "LONG" else "short"
    symbol = card.get("instrument", "XAU/USD")
    trade_type = card.get("trade_type") or "limit"
    dup = await _open_same_side_today(db, symbol, side, trade_type)
    if dup is not None:
        return {"skipped": "duplicate — same side/type already pending/open today", "id": dup.id}

    cap = can_open(await _live_positions(db), float(card.get("risk_usd") or 0.0))
    if not cap["ok"]:
        return {"skipped": f"exposure cap — {cap['reason']}", **cap}

    tps = [t.get("price") for t in card.get("targets", [])]
    tps += [None] * (4 - len(tps))
    row = GoldPosition(
        symbol=symbol, side=side, trade_type=trade_type,
        entry=float(entry), stop=float(stop), stop_initial=float(stop),
        limit_price=float(entry), expires_at=expires_at,
        tp1=tps[0], tp2=tps[1], tp3=tps[2], tp4=tps[3],
        lot=card.get("lot"), risk_usd=card.get("risk_usd"),
        profile=card.get("profile"), source=source,
        justification=(card.get("justification") or "")[:480],
        action="BUY" if side == "long" else "SELL",
        status="PENDING",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


async def monitor(db: AsyncSession, price: float,
                  now: Optional[_dt.datetime] = None) -> dict:
    """Advance PENDING (fill/cancel) and OPEN (TP/SL/time-stop) positions."""
    now = now or _utcnow()

    # 1) PENDING limits — fill on touch, cancel at expiry.
    pres = await db.execute(select(GoldPosition).where(GoldPosition.status == "PENDING"))
    filled, cancelled = [], []
    for row in list(pres.scalars().all()):
        exp = row.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=_dt.timezone.utc)
        action = pos.evaluate_pending(
            {"side": row.side, "limit_price": row.limit_price, "expires_at": exp}, price, now)
        if action["action"] == "fill":
            row.status = "OPEN"
            row.opened_at = now
            row.deadline = deadline_for(row.trade_type, now)
            filled.append({"id": row.id, "trade_type": row.trade_type, "entry": row.entry})
        elif action["action"] == "cancel":
            row.status = "CANCELLED"
            row.closed_at = now
            cancelled.append({"id": row.id, "trade_type": row.trade_type})
    if filled or cancelled:
        await db.commit()

    # 2) OPEN positions — trail/close.
    res = await db.execute(select(GoldPosition).where(GoldPosition.status == "OPEN"))
    rows = list(res.scalars().all())
    closed, updated = [], []
    for row in rows:
        opened = row.opened_at
        if opened is not None and opened.tzinfo is None:
            opened = opened.replace(tzinfo=_dt.timezone.utc)
        deadline = row.deadline
        if deadline is not None and deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=_dt.timezone.utc)
        state = {
            "side": row.side, "entry": row.entry, "stop": row.stop_initial,
            "targets": [t for t in (row.tp1, row.tp2, row.tp3, row.tp4) if t is not None],
            "be_active": bool(row.be_active), "opened_at": opened,
            "deadline": deadline, "trade_type": row.trade_type,
        }
        action = pos.evaluate(state, price, now)
        row.tp_hit = action["tp_hit"]
        row.be_active = action["be_active"]
        row.stop = action["stop"]
        if action["close"]:
            row.status = "CLOSED"
            row.exit_price = action["exit_price"]
            row.exit_reason = action["exit_reason"]
            row.result_r = action["result_r"]
            row.closed_at = now
            closed.append(_to_dict(row))
        else:
            updated.append({"id": row.id, "tp_hit": row.tp_hit,
                            "be_active": row.be_active, "note": action["note"]})
    await db.commit()
    return {"price": price, "checked": len(rows),
            "filled": filled, "cancelled": cancelled,
            "closed": closed, "still_open": updated}


async def list_positions(db: AsyncSession, status: Optional[str] = None,
                         limit: int = 50) -> List[dict]:
    stmt = select(GoldPosition).order_by(GoldPosition.opened_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(GoldPosition.status == status.upper())
    res = await db.execute(stmt)
    return [_to_dict(r) for r in res.scalars().all()]


def _to_dict(r: GoldPosition) -> dict:
    return {
        "id": r.id, "symbol": r.symbol, "side": r.side, "action": r.action,
        "trade_type": r.trade_type,
        "entry": r.entry, "stop": r.stop, "stop_initial": r.stop_initial,
        "targets": [t for t in (r.tp1, r.tp2, r.tp3, r.tp4) if t is not None],
        "lot": r.lot, "risk_usd": r.risk_usd, "be_active": r.be_active,
        "tp_hit": r.tp_hit, "profile": r.profile, "source": r.source,
        "status": r.status, "limit_price": r.limit_price,
        "exit_price": r.exit_price, "exit_reason": r.exit_reason,
        "result_r": r.result_r,
        "expires_at": str(r.expires_at) if r.expires_at else None,
        "opened_at": str(r.opened_at) if r.opened_at else None,
        "deadline": str(r.deadline) if r.deadline else None,
        "closed_at": str(r.closed_at) if r.closed_at else None,
    }
