"""EA setup log — every setup the MT5 EA finds (entered or blocked, and why), from every chart.

This is the data the owner collects while the EA runs on 25+ markets: which patterns appear,
at what quality, in which session, and which filter (spread, chop, news, council, …) stopped
them. Entries and strong blocked setups ping the owner's Telegram (throttled).
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Dict, List, Optional

KEY = "ea_setups"
KEEP = 3000
PING_MIN_QUALITY = 7
_pinged: Dict[str, float] = {}


def reason_of(outcome: str) -> str:
    """'BLOCKED: [CHOP-TRAP] ADX 19.0' → 'CHOP-TRAP'; 'COUNCIL: HOLD' → 'COUNCIL'; 'ENTERED' → 'ENTERED'."""
    o = (outcome or "").upper()
    if o.startswith("ENTERED"):
        return "ENTERED"
    if o.startswith("COUNCIL"):
        return "COUNCIL"
    body = o.split(":", 1)[1].strip() if ":" in o else o
    if body.startswith("["):
        return body[1:body.find("]")] if "]" in body else body
    return body.split(" ")[0] if body else "OTHER"


async def record(ev: Dict) -> Dict:
    from services import data_store
    row = {k: ev.get(k) for k in ("symbol", "side", "setup", "quality", "entry", "stop", "outcome", "tf", "session", "t")}
    row["t"] = int(row.get("t") or time.time())
    row["reason"] = reason_of(str(row.get("outcome") or ""))
    v, _ = await data_store.get(KEY)
    rows = list(v or [])
    if rows and all(rows[-1].get(k) == row.get(k) for k in ("symbol", "side", "setup", "outcome", "entry")):
        return {"ok": True, "duplicate": True}
    rows.append(row)
    await data_store.put(KEY, rows[-KEEP:])
    await _ping(row)
    return {"ok": True, "stored": len(rows[-KEEP:])}


async def _ping(row: Dict) -> None:
    entered = row["reason"] == "ENTERED"
    if not entered and int(row.get("quality") or 0) < PING_MIN_QUALITY:
        return
    k = f"{row.get('symbol')}:{row.get('side')}:{row['reason']}"
    if not entered and time.time() - _pinged.get(k, 0) < 3600:
        return
    _pinged[k] = time.time()
    try:
        from services.owner_notify import send
        icon = "🟢" if entered else "🧭"
        await send(f"{icon} EA {row.get('symbol')} {row.get('side')} {row.get('setup')} q{row.get('quality')}/10 "
                   f"@ {row.get('entry')} ({row.get('session') or ''}) — {row.get('outcome')}")
    except Exception:
        pass


async def recent(n: int = 15) -> List[Dict]:
    from services import data_store
    v, _ = await data_store.get(KEY)
    return list(v or [])[-n:][::-1]


def summary(rows: List[Dict], since: Optional[float] = None) -> Dict:
    rows = [r for r in rows if since is None or int(r.get("t") or 0) >= since]
    reasons = Counter(r.get("reason") for r in rows)
    return {"setups": len(rows), "entered": reasons.get("ENTERED", 0),
            "blocked": len(rows) - reasons.get("ENTERED", 0),
            "top_blocks": [(k, n) for k, n in reasons.most_common() if k != "ENTERED"][:4],
            "markets": len({r.get("symbol") for r in rows})}


def summary_line(s: Dict) -> str:
    if not s["setups"]:
        return "🤖 EA: no setups logged (owner sync key off, or no setups yet)."
    blocks = " · ".join(f"{k} {n}" for k, n in s["top_blocks"]) or "none"
    return (f"🤖 EA: {s['setups']} setups on {s['markets']} markets — {s['entered']} entered, "
            f"{s['blocked']} blocked ({blocks})")


def text(rows: List[Dict]) -> str:
    if not rows:
        return "🤖 No EA setups logged yet. They appear here once the EA (with your owner sync key) finds one."
    import datetime as dt
    lines = ["🤖 Latest EA setups (all charts):"]
    for r in rows:
        when = dt.datetime.fromtimestamp(int(r.get("t") or 0), dt.timezone.utc).strftime("%d %b %H:%M")
        icon = "🟢" if r.get("reason") == "ENTERED" else "⏸"
        lines.append(f"{icon} {when} {r.get('symbol')} {r.get('side')} {r.get('setup')} q{r.get('quality')} — "
                     f"{r.get('outcome')}")
    return "\n".join(lines)
