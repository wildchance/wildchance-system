"""MT5 execution bridge — runs on a WINDOWS VPS with the MetaTrader 5 terminal
and your FundingPips account logged in. It is NOT part of the Render app.

Loop:
  1. GET  {APP_BASE_URL}/execution/pending?token=EXECUTION_TOKEN
  2. place each order via MetaTrader5.order_send (market or OTE limit, with SL/TP)
  3. POST {APP_BASE_URL}/execution/ack   with the ticket / fill / rejection

Setup (Windows) — ONE connector per account:
  py -m pip install MetaTrader5 requests
  set APP_BASE_URL=https://wildchance-system.onrender.com
  set EXECUTION_TOKEN=<same token as the app env>
  set MT5_ACCOUNT=acc1           # fleet id — pulls ONLY this account's sized orders
  set MT5_LOGIN=12345678
  set MT5_PASSWORD=****
  set MT5_SERVER=FundingPips-Server
  set GOLD_SYMBOL=XAUUSD          # or XAUUSD.r / GOLD depending on the broker
  set MAX_VOLUME=0.10             # hard lot cap for THIS account
  py connector.py

5-account setups: run 5 copies (5 terminals, 5 windows on ONE VPS is fine — budget
~8 GB RAM), each with its own MT5_ACCOUNT=acc1..acc5 + its own MT5_LOGIN. Each
connector's ?account= filter pulls only its own orders — no duplicate fills. Two app
modes decide WHAT each account gets:
  • FLEET_ENABLED=true  — COPY one signal to all 5, sized per account (acc1 cent lots
    … acc5 layering lots). Same trade everywhere.
  • STRATEGY_ROUTING_ENABLED=true — DIFFERENT setup per account: each strategy's source
    routes to its own account via the STRATEGY_ACCOUNTS map (e.g. optimus→acc1,
    intraday→acc2, asian→acc3, scan→acc4, cbdr→acc5). Verify at GET /execution/routing.
Leave MT5_ACCOUNT unset for a single-account setup (all orders, no filter).

Prop-challenge kill-switch (equity_guard.py — copy it next to this file):
  set CHALLENGE_INITIAL_BALANCE=10000   # REQUIRED for a challenge: the starting balance
  set CHALLENGE_TARGET=0.08             # 0.08 phase 1, 0.05 phase 2, 0 = off
Every poll checks live equity: at 3.5% daily / 8% total drawdown (or the target) it
flattens this system's positions + pending orders and stops pulling new orders. The
state file survives restarts so the starting balance can never silently reset.
Multi-asset: set SYMBOL_MAP (JSON app→broker names); unset keeps it gold-only.

Safety: it only places orders the app already sized + prop-gated. Start on a
DEMO / practice account first. Set MAX_VOLUME to hard-cap lot size.
"""

import datetime as _dt
import json
import os
import time
import requests

try:                                    # same folder on the VPS
    from equity_guard import EquityGuard
except ImportError:                     # imported as a package (tests / repo)
    from mt5_bridge.equity_guard import EquityGuard

try:
    import MetaTrader5 as mt5
except Exception:                       # allows syntax/lint on non-Windows
    mt5 = None

APP = os.environ["APP_BASE_URL"].rstrip("/")
TOKEN = os.environ["EXECUTION_TOKEN"]
LOGIN = int(os.environ["MT5_LOGIN"])
PASSWORD = os.environ["MT5_PASSWORD"]
SERVER = os.environ["MT5_SERVER"]
SYMBOL = os.environ.get("GOLD_SYMBOL", "XAUUSD")
# Fleet account this terminal serves (acc1..acc5). Set it so this connector pulls
# ONLY its own sized orders; leave unset for a single-account setup (all orders).
ACCOUNT = os.environ.get("MT5_ACCOUNT")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))
MAX_VOLUME = float(os.environ.get("MAX_VOLUME", "1.0"))
DEVIATION = int(os.environ.get("DEVIATION", "20"))
MAGIC = 770001
# Multi-asset: app symbol → broker symbol, e.g. {"XAUUSD":"XAUUSD.r","EURUSD":"EURUSD.r"}.
# Unset = the original gold-only bridge (SYMBOL).
SYMBOL_MAP = json.loads(os.environ.get("SYMBOL_MAP", "{}") or "{}")
GUARD = EquityGuard.from_env()          # FundingPips/FTMO daily + max-loss kill-switch


def broker_symbol(app_symbol):
    """Map an app symbol to this broker's name. Gold-only unless SYMBOL_MAP is set."""
    key = (app_symbol or SYMBOL).upper().replace("/", "")
    if SYMBOL_MAP:
        return SYMBOL_MAP.get(key)
    return SYMBOL if key.startswith("XAU") else None


def guard_verdict():
    """Ask the equity guard whether new orders may go out (server-day aware)."""
    info, tick = mt5.account_info(), mt5.symbol_info_tick(SYMBOL)
    if info is None:
        return {"action": "TRADE", "note": "no account info"}
    server_day = _dt.datetime.utcfromtimestamp(tick.time).strftime("%Y-%m-%d") if tick \
        else _dt.datetime.utcnow().strftime("%Y-%m-%d")
    return GUARD.check(info.balance, info.equity, server_day)


def flatten(reason):
    """Close every position and delete every pending order carrying our MAGIC."""
    for p in mt5.positions_get() or []:
        if p.magic != MAGIC:
            continue
        tick = mt5.symbol_info_tick(p.symbol)
        buy = p.type == mt5.POSITION_TYPE_BUY
        mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
                        "volume": p.volume, "position": p.ticket,
                        "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                        "price": tick.bid if buy else tick.ask, "deviation": DEVIATION,
                        "magic": MAGIC, "comment": reason[:31],
                        "type_filling": mt5.ORDER_FILLING_IOC})
    for o in mt5.orders_get() or []:
        if o.magic == MAGIC:
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
    print(f"FLATTENED ({reason})")


def connect():
    if not mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    print(f"MT5 connected: {mt5.account_info().login} @ {SERVER}")


def _order_type(side, otype):
    if otype == "limit":
        return mt5.ORDER_TYPE_BUY_LIMIT if side == "buy" else mt5.ORDER_TYPE_SELL_LIMIT
    return mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL


def place(o):
    """Send one order to MT5. Returns (status, ticket, fill_price)."""
    vol = min(float(o["volume"]), MAX_VOLUME)
    if vol < 0.01:
        return "rejected", None, None
    is_limit = o["order_type"] == "limit"
    sym = broker_symbol(o.get("symbol"))
    if not sym or not mt5.symbol_select(sym, True):
        print(f"  order {o['id']} rejected: symbol {o.get('symbol')} not tradable here")
        return "rejected", None, None
    tick = mt5.symbol_info_tick(sym)
    price = o["price"] if is_limit else (tick.ask if o["side"] == "buy" else tick.bid)
    req = {
        "action": mt5.TRADE_ACTION_PENDING if is_limit else mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": round(vol, 2),
        "type": _order_type(o["side"], o["order_type"]),
        "price": float(price),
        "sl": float(o["sl"]) if o.get("sl") else 0.0,
        "tp": float(o["tp"]) if o.get("tp") else 0.0,
        "deviation": DEVIATION,
        "magic": int(o.get("magic") or 770001),
        "comment": (o.get("comment") or "wildchance")[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        print(f"  order {o['id']} rejected: {getattr(res, 'retcode', '?')} {mt5.last_error()}")
        return "rejected", None, None
    print(f"  order {o['id']} placed: ticket {res.order} @ {res.price}")
    return "filled" if not is_limit else "sent", res.order, res.price


def modify(o):
    """Move an existing position's stop/target (the runner's break-even trail). The order
    carries the MT5 ``ticket`` and the new ``sl``. Returns (status, ticket, fill_price)."""
    ticket = o.get("ticket")
    if not ticket:
        return "rejected", None, None
    pos = mt5.positions_get(ticket=int(ticket))
    if not pos:
        print(f"  modify {o['id']}: position {ticket} not found (already closed?)")
        return "rejected", None, None
    p = pos[0]
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": p.symbol,
        "position": int(ticket),
        "sl": float(o["sl"]) if o.get("sl") else p.sl,
        "tp": float(o["tp"]) if o.get("tp") else p.tp,
        "magic": int(o.get("magic") or 770001),
    }
    res = mt5.order_send(req)
    if res is None or res.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        print(f"  modify {o['id']} rejected: {getattr(res, 'retcode', '?')} {mt5.last_error()}")
        return "rejected", None, None
    print(f"  modify {o['id']}: position {ticket} SL→{req['sl']} (break-even)")
    return "filled", int(ticket), req["sl"]


def ack(order_id, status, ticket=None, fill_price=None):
    try:
        requests.post(f"{APP}/execution/ack", params={"token": TOKEN}, timeout=15,
                      json={"id": order_id, "status": status,
                            "ticket": ticket, "fill_price": fill_price})
    except Exception as e:
        print(f"  ack failed for {order_id}: {e}")


def loop():
    last_action = "TRADE"
    while True:
        try:
            verdict = guard_verdict()
            if verdict["action"] != "TRADE":
                if verdict["action"] != last_action:
                    flatten(verdict["action"])        # once per lock, not every poll
                    print(f"GUARD {verdict}")
                last_action = verdict["action"]
                time.sleep(POLL_SECONDS)
                continue                             # pull NO new orders while locked
            last_action = "TRADE"
            params = {"token": TOKEN}
            if ACCOUNT:
                params["account"] = ACCOUNT      # pull only this account's orders
            r = requests.get(f"{APP}/execution/pending", params=params, timeout=15)
            orders = r.json().get("orders", []) if r.ok else []
            for o in orders:
                if o.get("order_type") == "modify":
                    status, ticket, fill = modify(o)     # runner SL→break-even
                    ack(o["id"], status, ticket, fill)
                elif broker_symbol(o.get("symbol")):
                    status, ticket, fill = place(o)
                    ack(o["id"], status, ticket, fill)
                else:
                    ack(o["id"], "rejected")     # symbol not mapped on this bridge
        except Exception as e:
            print(f"poll error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if mt5 is None:
        raise SystemExit("MetaTrader5 package not available — run on a Windows VPS")
    connect()
    print(f"Bridge live — polling {APP}/execution/pending every {POLL_SECONDS}s")
    loop()
