"""SNIPER — precision entries at the artillery's levels.

The artillery (yearly / anchor / event boxes → multi-anchor golden clusters) marks
WHERE; the sniper decides WHEN, on the 1h chart, with the journal's trigger:

  1. RANGE   price is at a golden cluster (within its tolerance band)
  2. SWEEP   the 1h bars ran INTO the band (liquidity taken at the level)
  3. CISD    change in state of delivery — a 1h close back through the OPEN of the
             last opposing run of candles (bearish run for a BUY, bullish for a SELL)
  4. GATE    trade_gate (session, ≥1.3R, weekly profile) + the case library: the
             cluster's best ROLE must have held ≥ SNIPER_MIN_RATE recently (once n ≥ 3)
  5. COUNCIL the agent council (services.council) must vote GO — no macro veto

Entry = the CISD close · stop = beyond the sweep extreme (≥ the minimum stop) ·
targets = the next clusters in the trade direction. Every fire is recorded as a live
case and later resolved (TP1 before stop = held), so the role stats keep learning.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence

from decouple import config

MIN_RATE = float(config("SNIPER_MIN_RATE", default="0.5"))
LOOKBACK = int(config("SNIPER_LOOKBACK_H", default="12"))
DEFAULT_SYMBOLS = str(config("SNIPER_SYMBOLS", default="EUR/USD,GBP/USD,USD/JPY,XAU/USD,BTC/USD"))


def roles_of(members: Sequence[str]) -> List[str]:
    from services.yearly_cbdr import ladder_name
    out = []
    for m in members:
        try:
            r = ladder_name(float(m.rsplit(" ", 1)[-1]))
        except ValueError:
            r = None
        if r and r not in out:
            out.append(r)
    return out


def cisd(bars: Sequence[Dict], side: str, band: Sequence[float]) -> Dict:
    """CISD on oldest-first bars {open, high, low, close}. ``band`` = (low, high)."""
    if len(bars) < 4:
        return {"fired": False, "why": "not enough bars"}
    buy = side == "BUY"
    opposing = (lambda b: b["close"] < b["open"]) if buy else (lambda b: b["close"] > b["open"])
    last = bars[-1]
    j = len(bars) - 2
    while j >= 0 and not opposing(bars[j]):
        j -= 1
    if j < 0:
        return {"fired": False, "why": "no opposing run"}
    s = j
    while s - 1 >= 0 and opposing(bars[s - 1]):
        s -= 1
    level = bars[s]["open"]
    seg = bars[s:]
    swept = (min(b["low"] for b in seg) <= band[1]) if buy else (max(b["high"] for b in seg) >= band[0])
    crossed = last["close"] > level if buy else last["close"] < level
    extreme = min(b["low"] for b in seg) if buy else max(b["high"] for b in seg)
    return {"fired": bool(swept and crossed), "level": level, "extreme": extreme,
            "entry": last["close"], "swept": swept, "crossed": crossed,
            "why": None if swept and crossed else ("no sweep into the zone" if not swept
                                                   else "no close through the run's open")}


def candidates(r: Dict, bars: Sequence[Dict]) -> List[Dict]:
    """Golden clusters the recent 1h bars traded into; side from where price came from."""
    from services.yearly_cbdr import tolerance
    if not r.get("ok") or not bars:
        return []
    price = bars[-1]["close"]
    tol = tolerance(r["symbol"], price)
    recent = bars[-LOOKBACK:]
    lo, hi = min(b["low"] for b in recent), max(b["high"] for b in recent)
    before = bars[:-LOOKBACK] or bars[:1]
    came_from = sum(b["close"] for b in before) / len(before)
    out = []
    for c in r.get("clusters") or []:
        band = (c["low"] - tol / 2, c["high"] + tol / 2)
        if hi < band[0] or lo > band[1]:
            continue
        side = "BUY" if came_from > c["high"] else "SELL" if came_from < c["low"] else None
        if side:
            out.append({**c, "band": band, "side": side})
    return out


def targets_from(r: Dict, side: str, entry: float, stop: float) -> List[Dict]:
    from services.trade_gate import MIN_RR
    risk = abs(entry - stop)
    if risk <= 0:
        return []
    mids = sorted((c for c in r.get("clusters") or []
                   if (c["mid"] > entry if side == "BUY" else c["mid"] < entry)),
                  key=lambda c: abs(c["mid"] - entry))
    out, last = [], entry
    for c in mids:
        move = abs(c["mid"] - entry)
        if move < MIN_RR * risk or (out and abs(c["mid"] - last) < risk):
            continue
        out.append({"name": f"TP{len(out) + 1}", "price": c["mid"], "rr": round(move / risk, 2),
                    "level": ", ".join(c["members"][:2])})
        last = c["mid"]
        if len(out) == 3:
            break
    return out


def card(sym: str, s: Dict) -> str:
    from routes.cbdr import px
    rs = s.get("role_stats")
    lines = [f"🎯 *SNIPER {s['side']} — {sym}* (CISD at the artillery's level)",
             f"Zone {px(sym, s['zone'][0])}–{px(sym, s['zone'][1])}: " + ", ".join(s["members"]),
             f"Roles: {', '.join(s['roles']) or '—'}"
             + (f" · {rs['role']} held {rs['held']}/{rs['n']} (recent {rs['recent_rate']:.0%})" if rs else ""),
             f"Entry {px(sym, s['entry'])} · SL {px(sym, s['stop'])} (sweep {px(sym, s['extreme'])})",
             "   " + " · ".join(f"{t['name']} {px(sym, t['price'])} ({t['rr']}R)" for t in s["targets"])]
    if s.get("council"):
        from services.council import summary
        lines.append(summary(s["council"]))
    try:
        from propfirm.target_lots import card_lines
        lines += card_lines(sym, s["entry"], s["stop"], s["targets"])
    except Exception:
        pass
    return "\n".join(lines)


async def _bars_1h(symbol: str, n: int = 72) -> List[Dict]:
    from services.ohlc_service import fetch_ohlc_raw
    try:
        return await fetch_ohlc_raw(symbol, "1h", n)
    except Exception:
        return []


async def resolve_pending() -> Dict:
    """held = TP1 reached before the stop; broke = stop first (1h bars since the fire)."""
    try:
        from sqlalchemy import select
        from database.db import AsyncSessionLocal
        from models.case_model import LiveCase
    except Exception:
        return {"resolved": 0}
    done = 0
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(LiveCase).where(LiveCase.status == "pending"))).scalars().all()
            for c in rows:
                bars = await _bars_1h(c.symbol, 200)
                since = c.created_at.replace(tzinfo=None) if c.created_at else None
                for b in bars:
                    t = dt.datetime.fromisoformat(str(b["time"])[:19]) if b.get("time") else None
                    if since and t and t <= since:
                        continue
                    hit_stop = b["low"] <= c.stop if c.side == "BUY" else b["high"] >= c.stop
                    hit_tp = b["high"] >= c.tp1 if c.side == "BUY" else b["low"] <= c.tp1
                    if hit_stop or hit_tp:
                        c.status = "broke" if hit_stop else "held"    # same bar → conservative
                        c.resolved_at = dt.datetime.now(dt.timezone.utc)
                        done += 1
                        break
            await db.commit()
    except Exception:
        pass
    return {"resolved": done}


async def _record(sym: str, s: Dict) -> None:
    try:
        from database.db import AsyncSessionLocal
        from models.case_model import LiveCase
        async with AsyncSessionLocal() as db:
            db.add(LiveCase(symbol=sym, side=s["side"], roles="|".join(s["roles"]),
                            zone=f"{s['zone'][0]}-{s['zone'][1]}", entry=s["entry"], stop=s["stop"],
                            tp1=s["targets"][0]["price"], status="pending",
                            note=", ".join(s["members"])[:500]))
            await db.commit()
    except Exception:
        pass


async def scan(symbols: Optional[List[str]] = None, notify: bool = False) -> Dict:
    from services import yearly_cbdr as yc, case_library as cl, trade_gate as tg
    syms = symbols or [x.strip() for x in DEFAULT_SYMBOLS.split(",") if x.strip()]
    resolved = await resolve_pending()
    stats_all = await cl.all_cases()
    out, sent = [], []
    for sym in syms:
        r = await yc.run(sym)
        if not r.get("ok"):
            out.append({"symbol": sym, "skip": r.get("reason")})
            continue
        tol = yc.tolerance(sym, r["price"])
        if not any(abs(c["mid"] - r["price"]) <= 3 * tol + (c["high"] - c["low"]) for c in r["clusters"]):
            out.append({"symbol": sym, "skip": "no golden cluster near price"})   # saves a credit
            continue
        bars = await _bars_1h(sym)
        for c in candidates(r, bars):
            sig = cisd(bars, c["side"], c["band"])
            row = {"symbol": sym, "side": c["side"], "zone": [c["low"], c["high"]],
                   "members": c["members"], "roles": roles_of(c["members"]), "cisd": sig}
            if not sig["fired"]:
                row["held"] = sig["why"]
                out.append(row)
                continue
            buf = tol / 3
            stop = sig["extreme"] - buf if c["side"] == "BUY" else sig["extreme"] + buf
            stop = tg.widen_stop(sym, c["side"], sig["entry"], stop)
            tps = targets_from(r, c["side"], sig["entry"], stop)
            stats = cl.score(stats_all, symbol=None)
            rs = cl.best_role(stats, row["roles"])
            weekly = await tg.weekly_profile(sym)
            g = tg.evaluate(sym, c["side"], sig["entry"], stop, [t["price"] for t in tps], weekly=weekly)
            if not tps:
                g["reasons"].append("no target ≥ 1.3R")
            if rs and rs["n"] >= 3 and rs["recent_rate"] < MIN_RATE:
                g["reasons"].append(f"role {rs['role']} recent hit-rate {rs['recent_rate']:.0%} < {MIN_RATE:.0%}")
            from services import council as cn
            cv = await cn.convene(sym, c["side"], yearly=r, role_stats=rs)
            if cv["verdict"] != "GO":
                g["reasons"].append(f"council {cv['verdict']} (score {cv['score']}"
                                    + (f", veto {','.join(cv['veto'])}" if cv["veto"] else "") + ")")
            g["allow"] = not g["reasons"]
            row.update(entry=sig["entry"], extreme=sig["extreme"], stop=stop, targets=tps,
                       role_stats=rs, gate=g, council=cv)
            out.append(row)
            if notify and g["allow"]:
                from services.realtime import alert_once
                key = f"sniper:{sym}:{c['side']}:{c['low']}:{dt.date.today()}"
                if await alert_once(key, card(sym, row), ttl_hours=24):
                    await _record(sym, row)
                    sent.append(sym)
    return {"resolved": resolved, "results": out, "notified": sent}
