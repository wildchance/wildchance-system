"""Copy trader: one MASTER MT5 account → follower accounts at other brokers.

  MASTER  (WildChance_Copier EA, role MASTER) posts every open position each ~2 s
          → POST /bridge/copy/master
  FOLLOWER (same EA, role FOLLOWER, one per broker account) polls the target book
          → GET /bridge/copy?login=…&equity=…&ccy=… and reports what it holds
          → POST /bridge/copy/report

Lot mode: equity-scaled risk. follower lots = master lots × (follower equity / master
equity) × account multiplier — the EA then corrects for contract size and rounds to
the broker's volume step. Cent accounts (USC) are converted to dollars first.

Safety:
  - master older than COPY_STALE_S → followers HOLD (open nothing, close nothing):
    a disconnected master must never flatten the fleet
  - per-account pause (bot /copypause) and daily-loss stop (EA input, reported here)
  - entries skipped by the EA when price moved more than its slippage limit
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from decouple import config

STALE_S = float(config("COPY_STALE_S", default="30"))
ONLINE_S = 90

_master: Dict = {}                    # last master snapshot (+ "received" epoch)
_followers: Dict[str, Dict] = {}      # login → last report (+ "received")
_settings: Dict[str, Dict] = {}       # login → {paused, mult, name}
_loaded = {"v": False}


def usd(amount: float, ccy: Optional[str]) -> float:
    """Cent accounts (USC / US¢) report equity in cents."""
    c = (ccy or "USD").upper()
    return amount / 100.0 if c in ("USC", "USX", "US¢", "CENT") else amount


async def _load() -> None:
    if _loaded["v"]:
        return
    try:
        from services import data_store
        s, _ = await data_store.get("copy_accounts")
        _settings.update(s or {})
    except Exception:
        pass
    _loaded["v"] = True


async def _save() -> None:
    try:
        from services import data_store
        await data_store.put("copy_accounts", _settings)
    except Exception:
        pass


def _canon(sym: str) -> str:
    from services.broker_feed import canon
    return canon(sym)


async def set_master(snap: Dict, now: Optional[float] = None) -> Dict:
    await _load()
    pos = []
    for p in snap.get("positions") or []:
        try:
            pos.append({"id": str(p["ticket"]), "symbol": _canon(p["symbol"]), "broker_symbol": p["symbol"],
                        "side": "BUY" if str(p["side"]).upper() in ("BUY", "0") else "SELL",
                        "volume": float(p["volume"]), "open_price": float(p.get("open_price") or 0),
                        "sl": float(p.get("sl") or 0), "tp": float(p.get("tp") or 0),
                        "contract_size": float(p.get("contract_size") or 0) or None,
                        "open_time": int(p.get("open_time") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    _master.clear()
    _master.update({"login": str(snap.get("login") or ""), "broker": snap.get("broker") or "",
                    "equity_usd": usd(float(snap.get("equity") or 0), snap.get("ccy")),
                    "positions": pos, "received": now if now is not None else time.time()})
    return {"ok": True, "positions": len(pos)}


def master_age(now: Optional[float] = None) -> Optional[float]:
    if not _master.get("received"):
        return None
    return (now if now is not None else time.time()) - _master["received"]


async def targets(login: str, equity: float, ccy: Optional[str] = None,
                  now: Optional[float] = None) -> Dict:
    """What this follower should hold right now."""
    await _load()
    login = str(login)
    st = _settings.setdefault(login, {"paused": False, "mult": 1.0})
    age = master_age(now)
    base = {"login": login, "master_login": _master.get("login"), "master_age_s": round(age, 1) if age is not None else None,
            "paused": bool(st.get("paused")), "mult": float(st.get("mult") or 1.0)}
    if age is None or age > STALE_S:
        return {**base, "master_ok": False, "action": "HOLD", "reason": "master offline — holding, nothing closed",
                "positions": []}
    if login == _master.get("login"):
        return {**base, "master_ok": True, "action": "HOLD", "reason": "this account is the master", "positions": []}
    if st.get("paused"):
        return {**base, "master_ok": True, "action": "PAUSED", "reason": "paused by owner (new copies stop, "
                "open copies are managed)", "positions": _scaled(equity, ccy, base["mult"])}
    return {**base, "master_ok": True, "action": "COPY", "reason": "", "positions": _scaled(equity, ccy, base["mult"])}


def _scaled(equity: float, ccy: Optional[str], mult: float) -> List[Dict]:
    meq = _master.get("equity_usd") or 0
    ratio = (usd(equity, ccy) / meq) * mult if meq > 0 else 0
    return [{**p, "ratio": round(ratio, 6), "target_volume": round(p["volume"] * ratio, 4)}
            for p in _master.get("positions") or []]


async def report(rep: Dict, now: Optional[float] = None) -> Dict:
    await _load()
    login = str(rep.get("login") or "")
    if not login:
        return {"ok": False, "reason": "login missing"}
    _followers[login] = {**rep, "received": now if now is not None else time.time()}
    st = _settings.setdefault(login, {"paused": False, "mult": 1.0})
    if rep.get("name") and not st.get("name"):
        st["name"] = rep["name"]
    return {"ok": True}


async def control(login: str, paused: Optional[bool] = None, mult: Optional[float] = None) -> Dict:
    await _load()
    keys = list(_settings) if login == "all" else [str(login)]
    for k in keys:
        st = _settings.setdefault(k, {"paused": False, "mult": 1.0})
        if paused is not None:
            st["paused"] = paused
        if mult is not None:
            st["mult"] = max(0.0, min(float(mult), 10.0))
    await _save()
    return {"ok": True, "accounts": {k: _settings[k] for k in keys}}


async def status(now: Optional[float] = None) -> Dict:
    await _load()
    t = now if now is not None else time.time()
    age = master_age(t)
    accounts = []
    for login in sorted(set(_followers) | set(_settings)):
        if login == _master.get("login"):
            continue
        f, st = _followers.get(login, {}), _settings.get(login, {})
        seen = t - f["received"] if f.get("received") else None
        held = f.get("positions") or []
        accounts.append({"login": login, "name": st.get("name") or f.get("name") or "", "broker": f.get("broker") or "",
                         "online": seen is not None and seen < ONLINE_S,
                         "seen_s": round(seen, 1) if seen is not None else None,
                         "equity_usd": round(usd(float(f.get("equity") or 0), f.get("ccy")), 2) if f else None,
                         "copies": len(held), "missing": f.get("missing", 0),
                         "lag_ms": f.get("lag_ms"), "daily_pl_pct": f.get("daily_pl_pct"),
                         "guard": f.get("guard") or "ACTIVE", "paused": bool(st.get("paused")),
                         "mult": float(st.get("mult") or 1.0), "errors": (f.get("errors") or [])[-3:]})
    return {"master": {"login": _master.get("login"), "broker": _master.get("broker"),
                       "online": age is not None and age <= STALE_S,
                       "age_s": round(age, 1) if age is not None else None,
                       "equity_usd": round(_master.get("equity_usd") or 0, 2),
                       "positions": [{k: p[k] for k in ("symbol", "side", "volume", "open_price", "sl", "tp")}
                                     for p in _master.get("positions") or []]},
            "accounts": accounts, "stale_after_s": STALE_S}


def text(st: Dict) -> str:
    m = st["master"]
    if not m.get("login"):
        head = "👑 Master: not connected (attach WildChance_Copier, role MASTER)"
    else:
        head = (f"👑 Master {m['broker']} #{m['login']} {'🟢' if m['online'] else '🔴'} "
                f"${m['equity_usd']:,.0f} · {len(m['positions'])} open")
    lines = [head]
    for p in m["positions"][:8]:
        lines.append(f"   {p['symbol']} {p['side']} {p['volume']} @ {p['open_price']}")
    if not st["accounts"]:
        lines.append("No followers yet — attach the copier (role FOLLOWER) on each broker account.")
    for a in st["accounts"]:
        dot = "⏸" if a["paused"] else ("🟢" if a["online"] else "🔴")
        miss = f" · {a['missing']} not copied" if a["missing"] else ""
        lag = f" · {a['lag_ms']} ms" if a.get("lag_ms") is not None else ""
        guard = "" if a["guard"] == "ACTIVE" else f" · ⛔ {a['guard']}"
        eq = f"${a['equity_usd']:,.0f}" if a.get("equity_usd") is not None else "—"
        lines.append(f"{dot} {a['name'] or a['broker'] or 'account'} #{a['login']} {eq} · "
                     f"{a['copies']} copies · x{a['mult']:g}{miss}{lag}{guard}")
    return "\n".join(lines)
