"""DXY-flip alert — the single most important regime signal for gold.

The 2026 rule: gold is not structurally bullish until the dollar flips bearish.
This watches the live dollar read (RBUSBIS direction + the anticipated DXY fib
structure) and fires ONE Telegram alert when the lock state transitions:

    🔒 locked   → 🔓 unlocked   "DXY flipped bearish — gold longs UNLOCKED"
    🔓 unlocked → 🔒 locked     "dollar bid again — gold longs re-LOCKED"

Dedup is best-effort via a small marker file so a cron can call it every cycle and
it stays quiet until the state actually changes (``force`` overrides).
"""

from __future__ import annotations

import os
from typing import Optional

from gold import dxy as gdxy
from services import gold_scan  # reuse its Telegram sender

_STATE_DIR = os.environ.get("STATE_DIR", "state")
_MARKER = os.path.join(_STATE_DIR, "dxy_flip.state")


def _read_last() -> Optional[str]:
    try:
        with open(_MARKER) as f:
            return f.read().strip() or None
    except Exception:
        return None


def _write_last(state: str) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_MARKER, "w") as f:
            f.write(state)
    except Exception:
        pass


def _card(status: dict) -> str:
    icon = "🔓" if status["unlocked"] else "🔒"
    head = ("*DXY FLIP — gold longs UNLOCKED* 🔓" if status["unlocked"]
            else "*DXY — gold longs LOCKED* 🔒")
    lines = [
        f"{icon} {head}",
        "",
        f"Dollar regime: {status['dollar_regime']} / {status['phase']}",
        (f"DXY trigger: {status['dxy_trigger']}" if status.get("dxy_trigger") else ""),
        (f"RBUSBIS: {status['rbusbis_dir']}" if status.get("rbusbis_dir") else ""),
        (f"At extreme: {'yes' if status['at_extreme'] else 'no'}"),
        "",
        f"_{status['note']}_",
    ]
    return "\n".join(l for l in lines if l != "")


async def flip_alert(dxy_price: Optional[float] = None, notify: bool = True,
                     force: bool = False) -> dict:
    """Compute the current gold-long lock state and alert on transition.

    Pulls live RBUSBIS direction when FRED is configured; otherwise uses the
    anticipated DXY structure. Sends only when the state changed (or ``force``)."""
    rbusbis_dir = None
    try:
        from services import fred_service as fred
        if fred.configured():
            usd = await fred.dollar_read()
            if usd:
                rbusbis_dir = usd["direction"]
    except Exception:
        pass

    status = gdxy.dxy_flip_status(dxy_price, rbusbis_dir)
    state = status["gold_longs"]                 # "locked" | "unlocked"
    last = _read_last()
    changed = (last is not None and last != state)
    first_run = last is None

    sent = False
    should_send = force or changed
    if notify and should_send:
        sent = await gold_scan._tg(_card(status))
    _write_last(state)
    return {"sent": sent, "state": state, "previous": last,
            "changed": changed, "first_run": first_run,
            "rbusbis_dir": rbusbis_dir, "status": status}
