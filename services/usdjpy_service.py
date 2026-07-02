"""USD/JPY forward-test orchestration over the database.

Glues the pure engine (usdjpy/) to persistence (models/usdjpy_model.py):
  - ingest a daily close (manual or auto), compute MA20/SD20/z/signal
  - open a trade when a BUY/SELL fires
  - fill the day+3 exit and R for trades whose hold period has elapsed
  - build the live scoreboard
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.usdjpy_model import UsdJpyClose, UsdJpyTrade
from usdjpy.engine import (
    HOLD_DAYS,
    evaluate_close,
    is_new_stretch,
    result_r,
    realized_r,
    stop_breached,
    build_scoreboard,
)


def _to_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


async def _ordered_closes(db: AsyncSession) -> List[UsdJpyClose]:
    res = await db.execute(select(UsdJpyClose).order_by(UsdJpyClose.trade_date.asc()))
    return list(res.scalars().all())


async def ingest_close(
    db: AsyncSession,
    trade_date,
    close: float,
    source: str = "manual",
) -> dict:
    """Add/replace one daily close, recompute its row, open a trade if it fires.

    Idempotent on date: re-submitting the same trading day overwrites the close
    (handy for corrections). Returns the resulting signal plus any opened trade.
    """
    trade_date = _to_date(trade_date)
    close = float(close)

    existing = await db.execute(
        select(UsdJpyClose).where(UsdJpyClose.trade_date == trade_date)
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = UsdJpyClose(trade_date=trade_date, close=close, source=source)
        db.add(row)
    else:
        row.close = close
        row.source = source

    await db.flush()

    # Full chronological history including this close.
    history = await _ordered_closes(db)
    # Stats window = the closes up to AND INCLUDING this row's date, so
    # re-ingesting an out-of-order (older) date recomputes its ma20/sd20/z from
    # its OWN ≤date window instead of the latest tail. Makes seed/backfill
    # order-independent; for the normal daily append it's a no-op.
    closes = [c.close for c in history if c.trade_date <= trade_date]
    sig = evaluate_close(closes, date=trade_date.isoformat())

    row.ma20 = sig.ma20
    row.sd20 = sig.sd20
    row.z = sig.z
    row.signal = sig.action

    opened: Optional[UsdJpyTrade] = None
    trade_skipped: Optional[str] = None
    if sig.is_trade:
        # One position per stretch event (no-overlap, matches the backtest):
        # open only if no earlier trade's 3-day hold window still covers today.
        index_by_date = {c.trade_date: i for i, c in enumerate(history)}
        entry_idx = index_by_date.get(trade_date)
        existing = (await db.execute(select(UsdJpyTrade))).scalars().all()
        prior_idx = [
            index_by_date[t.entry_date]
            for t in existing
            if t.entry_date != trade_date and t.entry_date in index_by_date
        ]
        if any(t.entry_date == trade_date for t in existing):
            trade_skipped = "duplicate: a trade already exists for this date"
        elif entry_idx is not None and not is_new_stretch(entry_idx, prior_idx):
            trade_skipped = "continuation: within an open trade's hold window (no-overlap rule)"
        else:
            opened = UsdJpyTrade(
                entry_date=trade_date,
                action=sig.action,
                entry=sig.entry,
                stop=sig.stop,
                sd20=sig.sd20,
                stop_pips=sig.stop_pips,
                status="OPEN",
            )
            db.add(opened)

    filled = await fill_due_exits(db)
    await db.commit()

    return {
        "signal": sig.to_dict(),
        "opened_trade": _trade_dict(opened) if opened else None,
        "trade_skipped": trade_skipped,
        "exits_filled": filled,
    }


async def fill_due_exits(db: AsyncSession) -> List[dict]:
    """Close any OPEN trade whose day+3 close now exists.

    "3 trading days after entry" == 3 rows after the entry row in the daily log.
    """
    history = await _ordered_closes(db)
    index_by_date = {c.trade_date: i for i, c in enumerate(history)}

    open_res = await db.execute(
        select(UsdJpyTrade).where(UsdJpyTrade.status == "OPEN")
    )
    open_trades = list(open_res.scalars().all())

    filled: List[dict] = []
    for t in open_trades:
        entry_idx = index_by_date.get(t.entry_date)
        if entry_idx is None:
            continue
        exit_idx = entry_idx + HOLD_DAYS
        if exit_idx >= len(history):
            continue  # not enough trading days have passed yet

        exit_row = history[exit_idx]
        during = [c.close for c in history[entry_idx + 1: exit_idx + 1]]

        t.exit_date = exit_row.trade_date
        t.exit_price = exit_row.close
        t.result_r = result_r(t.action, t.entry, t.stop, exit_row.close)
        t.stop_breached = stop_breached(t.action, t.stop, during)
        t.status = "CLOSED"
        filled.append(_trade_dict(t))

    return filled


async def get_scoreboard(db: AsyncSession) -> dict:
    """Two tallies side by side: the workbook-faithful day+3 scoreboard (top
    level, unchanged) plus a stop-aware one (any stop-breached trade -> -1R),
    so you can compare before risking real money."""
    res = await db.execute(
        select(UsdJpyTrade.result_r, UsdJpyTrade.stop_breached).where(
            UsdJpyTrade.status == "CLOSED"
        )
    )
    rows = res.all()
    workbook_r = [r for (r, _) in rows if r is not None]
    stop_aware = [realized_r(r, bool(br)) for (r, br) in rows if r is not None]
    board = build_scoreboard(workbook_r).to_dict()
    board["mode"] = "workbook_faithful"
    board["stop_aware"] = build_scoreboard(stop_aware).to_dict()
    return board


async def list_trades(db: AsyncSession, limit: int = 200) -> List[dict]:
    res = await db.execute(
        select(UsdJpyTrade).order_by(UsdJpyTrade.entry_date.desc()).limit(limit)
    )
    return [_trade_dict(t) for t in res.scalars().all()]


async def list_closes(db: AsyncSession, limit: int = 200) -> List[dict]:
    res = await db.execute(
        select(UsdJpyClose).order_by(UsdJpyClose.trade_date.desc()).limit(limit)
    )
    return [_close_dict(c) for c in res.scalars().all()]


async def latest_signal(db: AsyncSession) -> Optional[dict]:
    res = await db.execute(
        select(UsdJpyClose).order_by(UsdJpyClose.trade_date.desc()).limit(1)
    )
    row = res.scalar_one_or_none()
    return _close_dict(row) if row else None


def _trade_dict(t: UsdJpyTrade) -> dict:
    return {
        "id": t.id,
        "entry_date": str(t.entry_date),
        "action": t.action,
        "entry": t.entry,
        "stop": t.stop,
        "sd20": t.sd20,
        "stop_pips": round(t.stop_pips, 1) if t.stop_pips is not None else None,
        "exit_date": str(t.exit_date) if t.exit_date else None,
        "exit_price": t.exit_price,
        "result_r": round(t.result_r, 4) if t.result_r is not None else None,
        "stop_breached": t.stop_breached,
        "status": t.status,
    }


def _close_dict(c: UsdJpyClose) -> dict:
    return {
        "trade_date": str(c.trade_date),
        "close": c.close,
        "ma20": round(c.ma20, 4) if c.ma20 is not None else None,
        "sd20": round(c.sd20, 4) if c.sd20 is not None else None,
        "z": round(c.z, 4) if c.z is not None else None,
        "signal": c.signal,
        "source": c.source,
    }
