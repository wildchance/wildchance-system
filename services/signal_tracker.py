"""Signal tracker — every card we send is followed to TP / SL on live prices, no MT5 needed.

record()  any engine (sniper, Z-setups, auto CBDR, swing, Asia→London) and the owner's
          manual signals (/signals/track) add entry / stop / targets. pending=True waits
          for price to reach the entry (limit / stop orders).
check()   every 5 min: walks 5-min bars since the signal (15-min once older than a day):
            fill (pending) → stop before TP1 = LOSS · TP1 = WIN, post a flash card at once,
            stop moves to entry · TP2 / TP3 = another card · back to entry after TP1 = the
            rest is breakeven (still a WIN) · last target = closed
          A signal still open after TRACK_EXPIRE_H (72) closes at the last price: within
          ±BE pips = BREAKEVEN, else win / loss. Same-bar stop + target counts the stop.
stats()   wins / losses / breakevens / win rate / pips — the signal journal.
Stored as JSON in Neon (data_store 'signal_log', newest 500).
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Dict, List, Optional

from decouple import config

EXPIRE_H = float(config("TRACK_EXPIRE_H", default="72"))
BE_PIPS = float(config("JOURNAL_BE_PIPS", default="1.0"))
KEY = "signal_log"
KEEP = 500


def _pips(symbol: str, side: str, a: float, b: float) -> float:
    from services.flash_card import pips
    return pips(symbol, side, a, b)


async def _load() -> List[Dict]:
    from services import data_store
    v, _ = await data_store.get(KEY)
    return list(v or [])


async def _save(rows: List[Dict]) -> None:
    from services import data_store
    await data_store.put(KEY, rows[-KEEP:])


def new_signal(symbol: str, side: str, entry: float, stop: float, targets: List[float], source: str,
               pending: bool = False, ts: Optional[float] = None) -> Dict:
    side = "BUY" if str(side).upper() in ("BUY", "LONG") else "SELL"
    tps = [float(t) for t in targets if t is not None]
    sgn = 1 if side == "BUY" else -1
    tps = sorted({t for t in tps if (t - entry) * sgn > 0}, key=lambda t: abs(t - entry))
    return {"id": uuid.uuid4().hex[:10], "symbol": symbol.upper(), "side": side, "entry": float(entry),
            "stop": float(stop), "targets": tps, "source": source, "pending": bool(pending),
            "filled": not pending, "sent_at": ts if ts is not None else time.time(), "hits": [], "result": "open",
            "stop_now": float(stop), "closed_at": None, "pips": None}


async def record(symbol: str, side: str, entry: float, stop: float, targets: List[float], source: str,
                 pending: bool = False) -> Optional[Dict]:
    try:
        s = new_signal(symbol, side, entry, stop, targets, source, pending)
        if not s["targets"] or (s["stop"] - s["entry"]) * (1 if s["side"] == "BUY" else -1) >= 0:
            return None
        rows = await _load()
        day = time.strftime("%Y-%m-%d", time.gmtime(s["sent_at"]))
        for r in rows:                         # same signal re-sent the same day → keep one
            if (r["symbol"], r["side"], r["source"], round(r["entry"], 3)) == \
                    (s["symbol"], s["side"], s["source"], round(s["entry"], 3)) and \
                    time.strftime("%Y-%m-%d", time.gmtime(r["sent_at"])) == day:
                return r
        rows.append(s)
        await _save(rows)
        return s
    except Exception:
        return None


def step(s: Dict, bars: List[Dict], now: Optional[float] = None) -> List[Dict]:
    """Advance one signal through bars (oldest-first, 'ts' unix). Returns events."""
    events: List[Dict] = []
    sgn = 1 if s["side"] == "BUY" else -1
    for b in bars:
        if b["ts"] < s.get("last_ts", s["sent_at"] - 300) or s["result"] != "open":
            continue
        s["last_ts"] = b["ts"] + 1
        hi, lo = b["high"], b["low"]
        if not s["filled"]:
            if (sgn < 0 and hi >= s["entry"]) or (sgn > 0 and lo <= s["entry"]):
                s["filled"] = True
                events.append({"kind": "filled"})
            else:
                continue
        adverse = lo if sgn > 0 else hi
        favour = hi if sgn > 0 else lo
        if (adverse - s["stop_now"]) * sgn <= 0:                       # stop (or breakeven) reached
            if s["hits"]:
                s["result"], close = "win", s["stop_now"]              # TP1 banked, rest at entry
            else:
                s["result"], close = "loss", s["stop"]
            s["pips"] = round(_pips(s["symbol"], s["side"], s["entry"], close), 1)
            s["closed_at"] = b["ts"]
            events.append({"kind": "closed", "result": s["result"]})
            break
        for i, t in enumerate(s["targets"]):
            if i < len(s["hits"]):
                continue
            if (favour - t) * sgn >= 0:
                s["hits"].append({"tp": i + 1, "price": t, "ts": b["ts"]})
                s["stop_now"] = s["entry"]                              # move to breakeven
                events.append({"kind": "tp", "tp": i + 1, "price": t})
            else:
                break
        if len(s["hits"]) == len(s["targets"]):
            s["result"] = "win"
            s["pips"] = round(_pips(s["symbol"], s["side"], s["entry"], s["targets"][-1]), 1)
            s["closed_at"] = b["ts"]
            events.append({"kind": "closed", "result": "win"})
            break
    now = now or time.time()
    if s["result"] == "open" and now - s["sent_at"] > EXPIRE_H * 3600:
        last = bars[-1]["close"] if bars else s["entry"]
        p = _pips(s["symbol"], s["side"], s["entry"], last) if s["filled"] else 0.0
        s["result"] = "win" if s["hits"] else ("breakeven" if abs(p) <= BE_PIPS or not s["filled"]
                                                else "win" if p > 0 else "loss")
        s["pips"], s["closed_at"] = round(p, 1), now
        events.append({"kind": "expired", "result": s["result"]})
    return events


def _bars_ts(bars: List[Dict]) -> List[Dict]:
    out = []
    for b in bars:
        try:
            t = dt.datetime.fromisoformat(str(b["time"])[:19]).replace(tzinfo=dt.timezone.utc)
            out.append({**b, "ts": t.timestamp()})
        except (KeyError, ValueError):
            continue
    return out


async def check() -> Dict:
    from services.flash_card import post
    from services.ohlc_service import fetch_ohlc_raw
    rows = await _load()
    open_rows = [r for r in rows if r["result"] == "open"]
    by_sym: Dict[str, List[Dict]] = {}
    for r in open_rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    posted, closed = [], []
    for sym, sigs in by_sym.items():
        oldest = min(s["sent_at"] for s in sigs)
        age_h = (time.time() - oldest) / 3600
        interval, n = ("5min", 300) if age_h <= 24 else ("15min", 300)
        try:
            bars = _bars_ts(await fetch_ohlc_raw(sym if "/" in sym or len(sym) != 6 else f"{sym[:3]}/{sym[3:]}",
                                                 interval, n, timezone="UTC"))
        except Exception:
            bars = []
        if not bars:
            continue
        for s in sigs:
            for e in step(s, bars):
                if e["kind"] == "tp":
                    when = dt.datetime.fromtimestamp(s["hits"][-1]["ts"], dt.timezone.utc)
                    r = await post(s["symbol"], s["side"], s["entry"], e["price"], when)
                    posted.append({"symbol": sym, "tp": e["tp"], "sent": r.get("sent")})
                elif e["kind"] in ("closed", "expired"):
                    closed.append({"symbol": sym, "result": e["result"], "pips": s["pips"]})
    if open_rows:
        await _save(rows)
    return {"open": sum(1 for r in rows if r["result"] == "open"), "flash_cards": posted, "closed": closed}


def summarise(rows: List[Dict]) -> Dict:
    done = [r for r in rows if r["result"] in ("win", "loss", "breakeven")]
    w = [r for r in done if r["result"] == "win"]
    l_ = [r for r in done if r["result"] == "loss"]
    be = [r for r in done if r["result"] == "breakeven"]
    decided = len(w) + len(l_)
    by_src: Dict[str, Dict] = {}
    for r in done:
        b = by_src.setdefault(r["source"], {"win": 0, "loss": 0, "breakeven": 0, "pips": 0.0})
        b[r["result"]] += 1
        b["pips"] = round(b["pips"] + (r["pips"] or 0), 1)
    return {"signals": len(rows), "open": sum(1 for r in rows if r["result"] == "open"),
            "closed": len(done), "wins": len(w), "losses": len(l_), "breakeven": len(be),
            "win_rate_pct": round(100 * len(w) / decided, 1) if decided else None,
            "pips": round(sum(r["pips"] or 0 for r in done), 1), "by_source": by_src}


async def stats() -> Dict:
    return summarise(await _load())
