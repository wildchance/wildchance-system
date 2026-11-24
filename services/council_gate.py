"""One council for EVERY engine — the owner's 4-for / 0-against rule before any card.

The sniper always convened the agent council; the other engines (auto CBDR signal,
swing fades, Z-setups, Asia→London) sent straight to Telegram. check() runs the same
council (cached intelligence, no paid calls) for any symbol + side; engines append its
line to the card and, with COUNCIL_GATE_ALL=true (default), only send on GO.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from decouple import config

GATE_ALL = str(config("COUNCIL_GATE_ALL", default="true")).lower() in ("1", "true", "yes", "on")
_scores: Dict = {"t": 0.0, "v": None}


def norm_side(side: str) -> str:
    s = str(side).upper()
    return "BUY" if s in ("BUY", "LONG", "UP", "BULL") else "SELL"


async def _agent_scores() -> Optional[Dict]:
    if _scores["v"] is not None and time.time() - _scores["t"] < 1800:
        return _scores["v"]
    try:
        from services import case_library as cl
        from services import council as cn
        seen, live = set(), []
        for c in await cl.all_cases():
            if c.get("source") == "live" and c.get("case_id") not in seen:
                seen.add(c.get("case_id"))
                live.append(c)
        from services import signal_tracker
        _scores.update(t=time.time(), v=cn.agent_scores(live + await signal_tracker.scoring_cases()))
    except Exception:
        _scores.update(t=time.time(), v={})
    return _scores["v"]


async def check(symbol: str, side: str, trade_type: str = "intraday", style: Optional[str] = None) -> Dict:
    """style: 'reversion' / 'continuation' for regime routing (None = no routing)."""
    from services import council as cn
    yearly = None
    try:
        from services import yearly_cbdr as yc
        r = await yc.run(symbol)
        yearly = r if r.get("ok") else None
    except Exception:
        yearly = None
    try:
        cv = await cn.convene(symbol, norm_side(side), yearly=yearly, trade_type=trade_type,
                              scores=await _agent_scores())
    except Exception as e:
        return {"pass": not GATE_ALL, "verdict": "ERROR", "line": f"🏛 Council unavailable ({type(e).__name__})"}
    from services import fx_rotation, regime
    ex = await fx_rotation.exposure_for(symbol, norm_side(side))
    try:
        rb = await regime.risk_for(symbol)
        blocked = regime.route(rb["regime"], style, norm_side(side))
    except Exception:
        rb, blocked = None, None
    line = cn.summary(cv) + ("\n" + ex["line"] if ex.get("line") else "") + \
        ("\n" + regime.line(rb) if rb else "") + (f"\n⛔ {blocked}" if blocked else "")
    ok = (cv["verdict"] == "GO" or not GATE_ALL) and ex["allow"] and not blocked
    verdict = cv["verdict"] if ex["allow"] else "EXPOSURE"
    verdict = "REGIME" if blocked else verdict
    return {"pass": ok, "verdict": verdict, "line": line, "for": cv["for"], "against": cv["against"],
            "risk_pct": (rb or {}).get("risk_pct"), "regime": (rb or {}).get("regime"),
            "votes": {v["agent"]: v["vote"] for v in cv.get("votes") or []}}
