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

5-account fleet: run 5 copies (5 terminals, 5 windows), each with its own
MT5_ACCOUNT=acc1..acc5 + its own MT5_LOGIN. Turn on FLEET_ENABLED=true in the app
so one signal fans out to per-account sized orders; each connector's ?account=
filter pulls only its own, so acc1 gets cent lots and acc5 the layering lots — no
duplicate fills. Leave MT5_ACCOUNT unset for a single-account setup (all orders).

Safety: it only places orders the app already sized + prop-gated. Start on a
DEMO / practice account first. Set MAX_VOLUME to hard-cap lot size.
"""

import os
import time
import requests

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
    tick = mt5.symbol_info_tick(SYMBOL)
    price = o["price"] if is_limit else (tick.ask if o["side"] == "buy" else tick.bid)
    req = {
        "action": mt5.TRADE_ACTION_PENDING if is_limit else mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
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


def ack(order_id, status, ticket=None, fill_price=None):
    try:
        requests.post(f"{APP}/execution/ack", params={"token": TOKEN}, timeout=15,
                      json={"id": order_id, "status": status,
                            "ticket": ticket, "fill_price": fill_price})
    except Exception as e:
        print(f"  ack failed for {order_id}: {e}")


def loop():
    while True:
        try:
            params = {"token": TOKEN}
            if ACCOUNT:
                params["account"] = ACCOUNT      # pull only this account's orders
            r = requests.get(f"{APP}/execution/pending", params=params, timeout=15)
            orders = r.json().get("orders", []) if r.ok else []
            for o in orders:
                if o.get("symbol", SYMBOL).upper().startswith("XAU"):
                    status, ticket, fill = place(o)
                    ack(o["id"], status, ticket, fill)
                else:
                    ack(o["id"], "rejected")     # non-gold, this bridge is gold-only
        except Exception as e:
            print(f"poll error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if mt5 is None:
        raise SystemExit("MetaTrader5 package not available — run on a Windows VPS")
    connect()
    print(f"Bridge live — polling {APP}/execution/pending every {POLL_SECONDS}s")
    loop()
