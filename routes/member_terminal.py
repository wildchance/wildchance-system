"""Subscriber terminal — the safe part of the owner's terminal, behind the member login.

  GET /members/terminal          the page (logs in with email + password or the API key, like /members)
  GET /members/terminal/data     X-API-Key → tiles by tier

PAID tiers: currency strength + pairings, event rotation, CME dealer walls on live spot, the
high-impact calendar, market regimes, COT / retail positioning and the TRACK RECORD (closed
signals only: result, pips, engine). FREE tier: a teaser (strongest / weakest currencies, the
calendar, the track-record summary) and an upgrade prompt.
NEVER shown to members: open signals and entries (those are the paid Telegram product),
exposure, backtests, the data audit and source health. Built once a minute for everyone.
"""

import os
import time

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from saas import tiers as st
from saas.auth import current_user

router = APIRouter(prefix="/members/terminal", tags=["members"])
_PAGE = os.path.join(os.path.dirname(__file__), "..", "static", "terminal", "members.html")
_cache = {"t": 0.0, "v": None}
TTL_S = 60


def track_record(rows, n: int = 15):
    from services import signal_tracker as stk
    done = [r for r in rows if r.get("result") in ("win", "loss", "breakeven")]
    s = stk.summarise(rows)
    recent = [{"symbol": r["symbol"], "side": r["side"], "result": r["result"], "pips": r.get("pips"),
               "engine": r.get("source"), "closed_at": r.get("closed_at")}
              for r in sorted(done, key=lambda r: r.get("closed_at") or 0, reverse=True)[:n]]
    return {"closed": s["closed"], "wins": s["wins"], "losses": s["losses"], "breakeven": s["breakeven"],
            "win_rate_pct": s["win_rate_pct"], "pips": s["pips"], "recent": recent}


def subscriber_view(full: dict, track: dict) -> dict:
    return {"as_of": full.get("as_of"), "macro": full.get("macro"), "strength": full.get("strength"),
            "pairings": full.get("pairings"),
            "rotation": {k: v for k, v in (full.get("rotation") or {}).items() if k in ("blocked", "rotate_to", "note")},
            "walls": {s: {k: w.get(k) for k in ("levels", "pc", "asof")} for s, w in (full.get("walls") or {}).items()},
            "calendar": full.get("calendar"), "regimes": {s: {k: r.get(k) for k in ("regime", "trend")}
                                                         for s, r in (full.get("regimes") or {}).items()},
            "positioning": full.get("positioning"), "track": track}


def teaser_view(sub: dict) -> dict:
    strength = sub.get("strength") or {}
    keys = list(strength)
    top = {c: strength[c] for c in keys[:2] + keys[-2:]}
    t = sub.get("track") or {}
    return {"as_of": sub.get("as_of"), "teaser": True, "strength": top, "calendar": sub.get("calendar"),
            "track": {k: t.get(k) for k in ("closed", "wins", "losses", "breakeven", "win_rate_pct", "pips")},
            "upgrade": "Upgrade to unlock the full currency matrix, event rotation, CME dealer walls, regimes, "
                       "positioning and the signal-by-signal track record."}


async def _subscriber_data() -> dict:
    if _cache["v"] and time.time() - _cache["t"] < TTL_S:
        return _cache["v"]
    from routes.terminal import build
    from services import signal_tracker as stk
    full = await build()
    v = subscriber_view(full, track_record(await stk._load()))
    _cache.update(t=time.time(), v=v)
    return v


@router.get("", response_class=HTMLResponse)
async def page():
    with open(_PAGE, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/data")
async def data(user=Depends(current_user)):
    sub = await _subscriber_data()
    paid = st.rank_of(user.tier) >= 1
    return {"tier": user.tier, "paid": paid, **(sub if paid else teaser_view(sub))}
