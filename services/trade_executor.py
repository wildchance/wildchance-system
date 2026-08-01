"""Trade executor — translate a computed signal into a broker order + queue it.

App-side only (broker-agnostic). `build_order` is pure and testable; enqueue /
pending / ack manage the queue the MT5 bridge consumes. Nothing here talks to
MT5 — that's the standalone connector on the VPS (mt5_bridge/connector.py).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from decouple import config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.execution_model import ExecutionOrder
from gold.risk_engine import GOLD_PIP

MAGIC = 770001                          # identifies this system's trades in MT5

# The runner scale-out plan — mirrors the validated sell-backtest 250/500 partials
# (price points: 250 pts = 2500 pips at GOLD_PIP 0.10, 500 pts = 5000 pips). Bank
# 0.34 at +250, 0.33 at +500, and carry the 0.33 runner to the final target with its
# stop trailed to BREAK-EVEN the moment the first partial (p1) fills. This is what
# closes the gap between the optimizer's modelled scale-out and live behaviour.
DEFAULT_EXIT_PARTIALS = ((250.0, 0.34), (500.0, 0.33))


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
    one is present (trend_targets). Falls back to a single base order otherwise.
    ``exit_style='partial'`` routes to the 250/500 runner break-even plan instead."""
    # Runner break-even scale-out (the validated 250/500 exit) when asked for it.
    if scale_out and sig.get("exit_style") == "partial":
        legs = plan_partial_exit(sig, symbol, source)
        if len(legs) >= 2:
            return legs
    return _build_ladder_orders(sig, symbol, source, scale_out)


def _build_ladder_orders(sig: dict, symbol: str, source: str,
                         scale_out: bool) -> List[dict]:
    """The trend-TP ladder / single-order build (no partial-exit branch — the shared
    fallback so build_orders and plan_partial_exit never recurse into each other)."""
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


def _lot_fractions(total: float, fracs, min_lot: float = 0.01, step: float = 0.01) -> List[float]:
    """Split ``total`` lots by ``fracs`` (the runner takes the remainder), every leg a
    whole ``step`` and >= ``min_lot``, summing EXACTLY to total. Degrades to fewer legs
    (nearest-first) when total can't afford them all — never emits a sub-min leg."""
    total_steps = int(round(round(float(total or 0), 2) / step))
    min_steps = max(1, int(round(min_lot / step)))
    weights = list(fracs) + [max(0.0, 1.0 - sum(fracs))]        # runner = remainder
    n = len(weights)
    affordable = min(n, total_steps // min_steps) if min_steps else 0
    if affordable < 1:
        return []
    weights = weights[:affordable]
    alloc = [min_steps] * affordable
    rem = total_steps - min_steps * affordable
    wsum = sum(weights) or 1.0
    prop = [int(rem * w // wsum) for w in weights]
    for i in range(affordable):
        alloc[i] += prop[i]
    leftover = rem - sum(prop)
    i = affordable - 1                                         # remainder to the runner
    while leftover > 0:
        alloc[i] += 1
        leftover -= 1
        i = (i - 1) if i > 0 else affordable - 1
    return [round(a * step, 2) for a in alloc]


def plan_partial_exit(sig: dict, symbol: str = "XAUUSD", source: str = "gold",
                      partials=DEFAULT_EXIT_PARTIALS) -> List[dict]:
    """Build the 250/500 scale-out legs for a sized signal — banking partials in price
    points as price runs in favour and carrying the runner to the final target with a
    break-even trail armed on the first partial fill.

    Legs share a ``group_id``; the runner carries ``be_price`` (entry) + ``be_after`` (p1)
    so ``breakeven_sweep`` moves its stop to BE once p1 fills. Falls back to the plain
    scale-out / single order when the lot can't afford multiple legs or geometry is off."""
    base = build_order(sig, symbol, source)
    if not base:
        return []
    entry = base.get("price")
    if entry is None:
        entry = float(sig.get("entry")) if sig.get("entry") is not None else None
    lot = float(sig.get("lot") or base.get("volume") or 0.0)
    # final target = the deepest TP on the card (the demand floor the runner rides to)
    tps = base.get("tp_levels") or ([base["tp"]] if base.get("tp") else [])
    if entry is None or lot <= 0 or not tps:
        return _build_ladder_orders(sig, symbol, source, True)
    sign = -1 if base["side"] == "sell" else 1                 # sell banks DOWN, buy banks UP
    final_tp = min(tps) if base["side"] == "sell" else max(tps)

    fracs = [f for _, f in partials]
    vols = _lot_fractions(lot, fracs)
    if len(vols) < 2:                                          # not enough size to scale out
        return _build_ladder_orders(sig, symbol, source, True)

    gid = uuid.uuid4().hex[:12]
    legs: List[dict] = []
    n = len(vols)
    for i, vol in enumerate(vols):
        leg = dict(base)
        leg["volume"] = vol
        leg["group_id"] = gid
        if i < n - 1:                                          # a banked partial
            dist = partials[i][0]
            leg["scale_role"] = f"p{i + 1}"
            leg["tp"] = round(entry + sign * dist, 2)
            leg["comment"] = (f"{sig.get('profile') or source} p{i + 1}")[:31]
        else:                                                  # the runner
            leg["scale_role"] = "runner"
            leg["tp"] = round(final_tp, 2)
            leg["be_price"] = round(float(entry), 2)           # trail to BREAK-EVEN
            leg["be_after"] = "p1"                             # armed by the first partial
            leg["comment"] = (f"{sig.get('profile') or source} run")[:31]
        legs.append(leg)
    return legs


def breakeven_modifications(legs: List[dict]) -> List[dict]:
    """Pure — given all legs of ONE group, if the arming partial (be_after) has FILLED,
    return the SL-to-BE modify order(s) for the still-live runner leg(s) not yet moved.

    A modify is a lightweight order the bridge honours via order_type='modify': it carries
    the runner's MT5 ``ticket`` and the ``be_price`` to set as the new stop. Emits nothing
    until the arming partial is filled AND the runner has a ticket (so it's placed)."""
    filled_roles = {l.get("scale_role") for l in legs if l.get("status") == "filled"}
    mods: List[dict] = []
    for l in legs:
        arm = l.get("be_after")
        if (arm and l.get("be_price") is not None and not l.get("be_done")
                and arm in filled_roles and l.get("ticket")
                and l.get("status") in ("sent", "filled")):
            mods.append({
                "symbol": l.get("symbol", "XAUUSD"), "side": l.get("side"),
                "order_type": "modify", "scale_role": "modify",
                "volume": l.get("volume") or 0.01, "price": None,
                "sl": l.get("be_price"), "tp": l.get("tp"),
                "ticket": l.get("ticket"), "group_id": l.get("group_id"),
                "magic": l.get("magic", MAGIC), "source": l.get("source"),
                "account": l.get("account"),
                "comment": (f"{l.get('source') or 'wildchance'} BE")[:31],
                "modifies_role": l.get("scale_role"),
            })
    return mods


async def breakeven_sweep(db: AsyncSession) -> dict:
    """Scan open scale-out groups and enqueue the runner's SL-to-BE modify once the first
    partial fills. Idempotent — flags each runner ``be_done`` so a modify is queued once.
    Safe to run on a schedule (cron) or right after an ack. No-op when execution is off."""
    if not EXECUTION_ENABLED:
        return {"execution_enabled": False, "modifies_queued": 0}
    rows = await recent(db, 400)
    groups: dict = {}
    for o in rows:
        gid = o.get("group_id")
        if gid:
            groups.setdefault(gid, []).append(o)

    queued = 0
    for gid, legs in groups.items():
        for mod in breakeven_modifications(legs):
            await enqueue(db, mod)
            # mark the runner leg be_done so we never double-queue the BE move
            runner = next((l for l in legs if l.get("scale_role") == mod["modifies_role"]), None)
            if runner and runner.get("id"):
                res = await db.execute(
                    select(ExecutionOrder).where(ExecutionOrder.id == runner["id"]))
                r = res.scalar_one_or_none()
                if r is not None:
                    r.be_done = 1
                    await db.commit()
            queued += 1
    return {"execution_enabled": True, "groups": len(groups), "modifies_queued": queued}


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
        # 250/500 runner break-even scale-out when the signal asks for it
        if sig.get("exit_style") == "partial":
            legs = plan_partial_exit(sig, source=source)
            if len(legs) >= 2:
                out = [await enqueue(db, leg) for leg in legs]
                return {"scale_out": out, "legs": len(out), "group_id": legs[0].get("group_id")}
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
        group_id=order.get("group_id"), scale_role=order.get("scale_role"),
        be_price=order.get("be_price"), be_after=order.get("be_after"),
        ticket=order.get("ticket"),
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
    out = _to_dict(row)
    # When a scale-out leg fills, arm the runner's break-even trail (best-effort — a
    # sweep failure must never fail the ack the bridge is waiting on).
    if status == "filled" and getattr(row, "group_id", None):
        try:
            out["breakeven"] = await breakeven_sweep(db)
        except Exception:
            pass
    return out


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
        "group_id": getattr(r, "group_id", None), "scale_role": getattr(r, "scale_role", None),
        "be_price": getattr(r, "be_price", None), "be_after": getattr(r, "be_after", None),
        "be_done": getattr(r, "be_done", 0),
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
