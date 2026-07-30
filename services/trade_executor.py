"""Trade executor — translate a computed signal into a broker order + queue it.

App-side only (broker-agnostic). `build_order` is pure and testable; enqueue /
pending / ack manage the queue the MT5 bridge consumes. Nothing here talks to
MT5 — that's the standalone connector on the VPS (mt5_bridge/connector.py).
"""

from __future__ import annotations

from typing import List, Optional

from decouple import config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.execution_model import ExecutionOrder

MAGIC = 770001                          # identifies this system's trades in MT5


def _env_num(key: str, default: float) -> float:
    """Read a numeric env var WITHOUT crashing boot on a malformed value. A typo like
    'my equity 111450' would make decouple's float-cast raise a ValueError at import
    time and take the whole API down — so parse defensively and fall back to default."""
    raw = config(key, default=None)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        import logging
        logging.getLogger("uvicorn.error").warning(
            "env %s is not a number (%r) — using default %s", key, raw, default)
        return float(default)


def _env_bool(key: str, default: bool) -> bool:
    """Boolean env var that never raises: true/1/yes/on → True; anything else → default."""
    raw = config(key, default=None)
    if raw is None or raw == "":
        return bool(default)
    return str(raw).strip().lower() in ("true", "1", "yes", "on", "y", "t")


# The single live-execution switch. Default OFF (paper). Flip to true in the app
# env ONLY once the MT5 VPS bridge is up — then every tracked position also
# enqueues a broker order for the connector to place. No code change to go live.
EXECUTION_ENABLED = _env_bool("EXECUTION_ENABLED", False)
# When on, one signal fans out to the 5-account fleet (each order sized per account
# + tagged account=accN); each VPS connector pulls only its own via ?account=.
FLEET_ENABLED = _env_bool("FLEET_ENABLED", False)
# Portfolio VaR gate (Phase 10). OFF by default → fail-open (never blocks). When enabled
# with a set equity, a new order is BLOCKED if it pushes portfolio VaR over the budget.
PORTFOLIO_VAR_GATE_ENABLED = _env_bool("PORTFOLIO_VAR_GATE_ENABLED", False)
PORTFOLIO_EQUITY_USD = _env_num("PORTFOLIO_EQUITY_USD", 0.0)
PORTFOLIO_VAR_LIMIT_PCT = _env_num("PORTFOLIO_VAR_LIMIT_PCT", 5.0)


async def var_gate(db: AsyncSession, sig: dict, source: str = "gold") -> dict:
    """Portfolio VaR/ES verdict for adding this signal to the open book. Fail-OPEN:
    disabled, no equity, or any error → approved, so it never silently breaks execution.
    Only BLOCKS when explicitly enabled AND the resulting VaR exceeds the budget."""
    if not PORTFOLIO_VAR_GATE_ENABLED or PORTFOLIO_EQUITY_USD <= 0:
        return {"approved": True, "reason": "VaR gate disabled"}
    try:
        from gold import portfolio_risk as pr
        from services.gold_positions import list_positions, _to_dict
        from services.ohlc_service import fetch_ohlc
        rows = await list_positions(db, status="open")
        positions = []
        for r in rows:
            d = _to_dict(r) if not isinstance(r, dict) else r
            positions.append({"side": d.get("side", "buy"),
                              "lot": d.get("lot", d.get("size", 0.01)),
                              "price": d.get("current_price", d.get("entry", d.get("entry_price")))})
        # marginal exposure of the new (fleet-summed) order
        new_lot = sum(float(o["volume"]) for o in build_fleet_orders(sig, source)) or float(sig.get("lot") or 0.01)
        new_order = {"side": "buy" if sig.get("signal") in ("LONG", "BUY") else "sell",
                     "lot": new_lot, "price": sig.get("entry")}
        bars = await fetch_ohlc("XAU/USD", "1day", 40)
        closes = [float(b[4]) for b in bars] if bars else []
        returns = [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
        return pr.risk_gate(positions, PORTFOLIO_EQUITY_USD, returns,
                            PORTFOLIO_VAR_LIMIT_PCT, new_order=new_order)
    except Exception:
        return {"approved": True, "reason": "VaR gate error — fail-open"}


def fleet_accounts() -> list:
    """The linked accounts to fan out to. Override via FLEET_ACCOUNTS env (JSON list
    of {id, balance, denom, risk_pct}); else built from the fleet registry defaults."""
    import json
    raw = config("FLEET_ACCOUNTS", default=None)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    from gold.accounts import FLEET
    return [{"id": aid, "balance": m["default_deposit"], "denom": "USD", "risk_pct": 1.0}
            for aid, m in FLEET.items()]


def build_fleet_orders(sig: dict, source: str = "gold") -> list:
    """Fan one signal into per-account MT5 orders via copy_fanout (each sized to its
    account's balance × risk, tagged account=accN)."""
    if sig.get("signal") not in ("LONG", "SHORT", "BUY", "SELL"):
        return []
    if not sig.get("gate", {}).get("allow", True):
        return []
    if sig.get("entry") is None or sig.get("stop") is None:
        return []
    from gold.accounts import copy_fanout
    side = "long" if sig["signal"] in ("LONG", "BUY") else "short"
    fan = copy_fanout({"signal": sig["signal"], "side": side, "entry": sig["entry"],
                       "stop": sig["stop"], "targets": sig.get("targets", [])},
                      fleet_accounts())
    if not fan.get("ok"):
        return []
    otype = "limit" if (sig.get("entry_mode") == "structure"
                        or sig.get("kind") == "limit") else "market"
    tps = [t.get("price") for t in sig.get("targets", []) if t.get("price") is not None]
    mt5_side = "buy" if side == "long" else "sell"
    orders = []
    for i, leg in enumerate(fan["fanout"], start=1):
        orders.append({
            "symbol": "XAUUSD", "side": mt5_side, "order_type": otype,
            "volume": float(leg["lot"]),
            "price": float(sig["entry"]) if otype == "limit" else None,
            "sl": float(sig["stop"]) if sig.get("stop") is not None else None,
            "tp": float(tps[0]) if tps else None, "tp_levels": tps,
            "magic": MAGIC + i, "account": leg["account"],
            "comment": (f"{leg['account']}:{sig.get('profile') or source}")[:31],
            "source": source,
        })
    return orders


def plan_scale_out(total: float, tps: list, min_lot: float = 0.01,
                   step: float = 0.01) -> List[dict]:
    """Split a position into front-loaded partial legs across a TP ladder.

    The nearest TP gets the most volume (weights n, n-1, … 1). Every leg is >= min_lot
    and a whole number of `step`; the legs sum EXACTLY to `total`. If `total` can only
    afford k legs at min_lot, only the nearest k TPs are used (never sub-min legs)."""
    total = round(float(total or 0), 2)
    tps = list(tps or [])
    if not tps:
        return []
    min_steps = max(1, int(round(min_lot / step)))
    total_steps = int(round(total / step))
    if total_steps < min_steps:
        return []
    max_legs = min(len(tps), total_steps // min_steps)
    if max_legs < 1:
        return []
    weights = [max_legs - i for i in range(max_legs)]     # front-loaded: n, n-1, …, 1
    wsum = sum(weights)
    alloc = [min_steps] * max_legs
    rem = total_steps - min_steps * max_legs
    prop = [rem * w // wsum for w in weights]
    for i in range(max_legs):
        alloc[i] += prop[i]
    leftover = rem - sum(prop)
    i = 0
    while leftover > 0:                                    # remainder to the front legs
        alloc[i % max_legs] += 1
        leftover -= 1
        i += 1
    return [{"volume": round(alloc[i] * step, 2), "tp": tps[i]} for i in range(max_legs)]


def build_orders(sig: dict, symbol: str = "XAUUSD", source: str = "gold",
                 scale_out: bool = True) -> List[dict]:
    """Build MT5 order(s) for a signal, scaling the lot across the trend-TP ladder when
    one is present (trend_targets). Falls back to a single base order otherwise."""
    base = build_order(sig, symbol, source)
    if not base:
        return []
    ladder = ((sig.get("trend_targets") or {}).get("targets")) or []
    tps = [t.get("price") for t in ladder if t.get("price") is not None]
    lot = float(sig.get("lot") or 0.0)
    if scale_out and len(tps) >= 2:
        legs = plan_scale_out(lot, tps)
        if len(legs) >= 2:
            n = len(legs)
            orders = []
            for i, leg in enumerate(legs, start=1):
                o = dict(base)
                o["volume"] = leg["volume"]
                o["tp"] = leg["tp"]
                o["scale_leg"] = f"{i}/{n}"
                o["comment"] = (f"{sig.get('profile') or source} {i}/{n}")[:31]
                orders.append(o)
            return orders
    return [base]


def build_order(sig: dict, symbol: str = "XAUUSD", source: str = "gold") -> Optional[dict]:
    """Normalize a gold signal into an MT5-ready order, or None if not tradeable.

    Structure entries (Wade OTE) become LIMIT orders at the OTE price; otherwise
    a MARKET order at the signal entry. SL/TP come straight from the sized card.
    """
    if sig.get("signal") not in ("LONG", "SHORT", "BUY", "SELL"):
        return None
    if not sig.get("gate", {}).get("allow", True):
        return None
    side = "buy" if sig["signal"] in ("LONG", "BUY") else "sell"
    # Structure/OTE entries AND pre-London/CRT/S&D limit cards become LIMIT orders
    # at their entry price; everything else is a market order.
    otype = "limit" if (sig.get("entry_mode") == "structure"
                        or sig.get("kind") == "limit") else "market"
    tps = [t.get("price") for t in sig.get("targets", []) if t.get("price") is not None]
    return {
        "symbol": symbol,
        "side": side,
        "order_type": otype,
        "volume": float(sig.get("lot") or 0.0),
        "price": float(sig["entry"]) if otype == "limit" else None,
        "sl": float(sig["stop"]) if sig.get("stop") is not None else None,
        "tp": float(tps[0]) if tps else None,          # TP1; bridge can scale out to the rest
        "tp_levels": tps,
        "magic": MAGIC,
        "comment": (sig.get("profile") or source)[:31],
        "source": source,
    }


async def maybe_enqueue(db: AsyncSession, sig: dict, source: str = "gold") -> Optional[dict]:
    """Enqueue an MT5 order for the bridge — but ONLY when live execution is on.

    Called from the position-open path so that flipping EXECUTION_ENABLED=true is
    all it takes to route the same tracked trades to the broker. Paper (OFF) is a
    no-op. Never raises: a broker-queue failure must not block the tracked open."""
    if not EXECUTION_ENABLED:
        return None
    gate = await var_gate(db, sig, source)
    if not gate.get("approved", True):
        return {"blocked": True, "risk_gate": gate}       # portfolio VaR over budget
    try:
        if FLEET_ENABLED:
            orders = build_fleet_orders(sig, source)
            out = [await enqueue(db, o) for o in orders]
            return {"fleet": out, "accounts": len(out)} if out else None
        order = build_order(sig, source=source)
        if not order:
            return None
        return await enqueue(db, order)
    except Exception:
        return None


async def enqueue(db: AsyncSession, order: dict) -> dict:
    """Persist a pending order for the bridge to pull."""
    row = ExecutionOrder(
        symbol=order["symbol"], side=order["side"], order_type=order["order_type"],
        volume=order["volume"], price=order.get("price"), sl=order.get("sl"),
        tp=order.get("tp"), magic=order.get("magic", MAGIC),
        comment=order.get("comment"), source=order.get("source"),
        account=order.get("account"), status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "status": row.status, **order}


async def pending(db: AsyncSession, limit: int = 20,
                  account: str = None) -> List[dict]:
    """Pending orders for the bridge. ``account`` filters to one fleet account
    (acc1..acc5) so each VPS connector only pulls — and acks — its own orders."""
    q = select(ExecutionOrder).where(ExecutionOrder.status == "pending")
    if account:
        q = q.where(ExecutionOrder.account == account)
    q = q.order_by(ExecutionOrder.created_at.asc()).limit(limit)
    res = await db.execute(q)
    return [_to_dict(r) for r in res.scalars().all()]


async def ack(db: AsyncSession, order_id: int, status: str,
              ticket: Optional[int] = None, fill_price: Optional[float] = None) -> dict:
    res = await db.execute(select(ExecutionOrder).where(ExecutionOrder.id == order_id))
    row = res.scalar_one_or_none()
    if row is None:
        return {"error": f"order {order_id} not found"}
    row.status = status
    if ticket is not None:
        row.ticket = ticket
    if fill_price is not None:
        row.fill_price = fill_price
    await db.commit()
    return _to_dict(row)


async def recent(db: AsyncSession, limit: int = 50) -> List[dict]:
    res = await db.execute(
        select(ExecutionOrder).order_by(ExecutionOrder.created_at.desc()).limit(limit))
    return [_to_dict(r) for r in res.scalars().all()]


def _to_dict(r: ExecutionOrder) -> dict:
    return {
        "id": r.id, "symbol": r.symbol, "side": r.side, "order_type": r.order_type,
        "volume": r.volume, "price": r.price, "sl": r.sl, "tp": r.tp,
        "magic": r.magic, "comment": r.comment, "source": r.source,
        "account": getattr(r, "account", None),
        "status": r.status, "ticket": r.ticket, "fill_price": r.fill_price,
        "created_at": str(r.created_at) if r.created_at else None,
    }


async def reconcile(db: AsyncSession, stuck_minutes: int = 15) -> dict:
    """Drift guard for live execution — compare the MT5 bridge's order state against
    the tracked gold positions and flag anything out of sync.

    Catches: orders the bridge filled with no matching OPEN position (a fill the
    tracker doesn't know about), orders stuck pending/sent past ``stuck_minutes``
    (bridge not polling / VPS down), and the raw status tally. ``in_sync`` is True
    only when there is no drift. Read-only — run it on a schedule once live."""
    import datetime as _dt
    orders = await recent(db, 300)
    from services import gold_positions as gp
    positions = await gp.list_positions(db, limit=300)

    by_status: dict = {}
    for o in orders:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1

    filled = [o for o in orders if o["status"] == "filled"]
    rejected = [o for o in orders if o["status"] == "rejected"]

    now = _dt.datetime.now(_dt.timezone.utc)
    stuck = []
    for o in orders:
        if o["status"] in ("pending", "sent") and o.get("created_at"):
            try:
                ct = _dt.datetime.fromisoformat(o["created_at"])
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=_dt.timezone.utc)
                if (now - ct).total_seconds() > stuck_minutes * 60:
                    stuck.append(o)
            except Exception:
                pass

    open_pos = [p for p in positions if (p.get("status") == "OPEN")]
    open_keys = {(p.get("source"), p.get("side")) for p in open_pos}
    # a filled order whose (source, side) has no OPEN tracked position = drift
    orphan_fills = [o for o in filled if (o.get("source"), o.get("side")) not in open_keys]

    drift = len(orphan_fills) + len(stuck)
    return {
        "orders_total": len(orders), "by_status": by_status,
        "filled": len(filled), "rejected": len(rejected),
        "open_positions": len(open_pos),
        "stuck_count": len(stuck), "stuck_pending": stuck,
        "orphan_fill_count": len(orphan_fills), "orphan_fills": orphan_fills,
        "drift": drift, "in_sync": drift == 0,
        "execution_enabled": EXECUTION_ENABLED,
        "note": ("bridge and tracker agree" if drift == 0 else
                 f"{drift} drift item(s): {len(orphan_fills)} orphan fill(s), "
                 f"{len(stuck)} stuck order(s) — check the VPS bridge"),
    }
