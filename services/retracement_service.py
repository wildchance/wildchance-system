"""Live retracement read + transition alert + optional paper auto-feed.

One home for the gold.retracement plumbing so every consumer stays DRY:

  • live_read(...)  — fetch HTF bars + the fused HTF ORB bias + the pre-London CBDR
    box + the DXY flip state, and return the classified state (SELL-the-OTE /
    scalp-the-bounce / LEAVE). Used by GET /gold/retracement, the STRATOPS muster,
    and the recon board so the state is visible without a second call.
  • state_alert(...) — fire ONE Telegram when the state TRANSITIONS (dedup via a
    marker file, mirroring dxy_flip / recon), and — on a transition into SELL_OTE
    with ``deploy`` — hand the card straight to gold_positions.open_from_signal
    (paper) so the read and the trade are a single step.
"""

from __future__ import annotations

import os
from typing import Optional

from gold import retracement as gret
from gold import radar as grd
from gold import dxy as gdxy
from gold import risk_engine as grisk
from services import gold_scan

_STATE_DIR = os.environ.get("STATE_DIR", "state")
_MARKER = os.path.join(_STATE_DIR, "retracement.state")


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


async def _htf_bias() -> Optional[str]:
    """Fused daily/weekly/monthly HTF ORB bias — the trend filter."""
    try:
        from services.ohlc_service import fetch_ohlc
        daily = await fetch_ohlc("XAU/USD", "1day", 90)
        weekly = await fetch_ohlc("XAU/USD", "1week", 60)
        monthly = await fetch_ohlc("XAU/USD", "1month", 48)
        return grd.combine_htf(
            daily=grd.order_blocks(daily, timeframe="1D") if len(daily) >= 8 else [],
            weekly=grd.order_blocks(weekly, timeframe="1W") if len(weekly) >= 8 else [],
            monthly=grd.order_blocks(monthly, timeframe="1M") if len(monthly) >= 8 else [],
        ).get("htf_bias")
    except Exception:
        return None


async def _box(window: str):
    try:
        from services.recon_service import _live_box
        return await _live_box(window)
    except Exception:
        return None


async def live_read(gold_price: Optional[float] = None, interval: str = "4h",
                    bars: int = 40, window: str = "prelondon",
                    htf_bias: Optional[str] = None, box=None,
                    dxy_unlocked: Optional[bool] = None) -> dict:
    """The live 3-state retracement read. Callers may pre-supply ``htf_bias`` /
    ``box`` / ``dxy_unlocked`` (the muster already has them) to skip refetching."""
    from services.ohlc_service import fetch_ohlc_raw
    raw = await fetch_ohlc_raw("XAU/USD", interval=interval, outputsize=bars)
    if len(raw) < 8:
        return {"state": "LEAVE", "actionable": False,
                "reason": "not enough XAU/USD HTF bars", "interval": interval}
    obars = gret.to_ohlc(raw)
    if gold_price is None:
        try:
            from utils.price_fetcher import get_forex_price
            gold_price = await get_forex_price("XAU/USD")
        except Exception:
            gold_price = obars[-1][3]
    if htf_bias is None:
        htf_bias = await _htf_bias()
    if dxy_unlocked is None:
        try:
            dxy_unlocked = bool(gdxy.dxy_flip_status().get("unlocked"))
        except Exception:
            dxy_unlocked = False
    if box is None:
        box = await _box(window)
    read = gret.retracement_state(obars, price=float(gold_price), htf_bias=htf_bias,
                                  box=box, dxy_unlocked=bool(dxy_unlocked))
    read["interval"] = interval
    read["price"] = round(float(gold_price), 2)
    read["dxy_unlocked"] = bool(dxy_unlocked)
    read["display"] = gret.format_retracement(read)
    return read


def summary(read: dict) -> dict:
    """Compact board/muster field — the state at a glance without the full payload."""
    if not read:
        return {"state": None}
    return {"state": read.get("state"), "label": read.get("label"),
            "actionable": read.get("actionable"), "signal": read.get("signal"),
            "retracement": read.get("retracement"), "reason": read.get("reason")}


def _sell_card(read: dict, balance: float, risk_usd: float) -> Optional[dict]:
    """Build a paper signal card from a SELL_OTE read for open_from_signal."""
    entry, stop = read.get("entry"), read.get("stop")
    if entry is None or stop is None:
        return None
    from propfirm.engine import evaluate_trade, DEFAULT_TIER
    lot = max(grisk.MIN_LOT, grisk.size_for_risk(entry, stop, risk_usd))
    return {
        "signal": "SHORT", "instrument": "XAU/USD",
        "trade_type": read.get("trade_type") or "swing",
        "entry": entry, "stop": stop, "lot": lot, "risk_usd": risk_usd,
        "targets": read.get("targets") or [],
        "gate": evaluate_trade(balance, risk_usd, tier=DEFAULT_TIER),
        "profile": "retracement_sell_ote",
        "justification": read.get("reason"),
    }


async def auto_feed(db, read: dict, balance: float = 5000.0,
                    risk_usd: float = 20.0):
    """Hand a SELL_OTE read to the paper position tracker (deduped by that layer)."""
    if not read or read.get("state") != "SELL_OTE" or not read.get("actionable"):
        return None
    from services import gold_positions as gp
    card = _sell_card(read, balance, risk_usd)
    if not card:
        return None
    return await gp.open_from_signal(db, card, source="retracement_paper")


async def state_alert(gold_price: Optional[float] = None, interval: str = "4h",
                      window: str = "prelondon", notify: bool = True,
                      force: bool = False, deploy: bool = False, db=None,
                      balance: float = 5000.0, risk_usd: float = 20.0) -> dict:
    """Compute the state and alert on a TRANSITION only (dedup marker).

    ``deploy=True`` (with a db session) also opens the paper position on a fresh
    transition INTO SELL_OTE. Cron-friendly: stays quiet until the state changes."""
    read = await live_read(gold_price=gold_price, interval=interval, window=window)
    state = read.get("state")
    last = _read_last()
    changed = last is not None and last != state
    first_run = last is None

    sent = False
    if notify and (force or changed):
        sent = await gold_scan._tg(read.get("display") or str(state))
    _write_last(state)

    deployed = None
    # only auto-feed on a genuine transition INTO SELL_OTE (not every poll)
    if deploy and db is not None and state == "SELL_OTE" and (changed or first_run):
        deployed = await auto_feed(db, read, balance, risk_usd)

    return {"sent": sent, "state": state, "previous": last, "changed": changed,
            "first_run": first_run, "deployed": deployed, "read": read}
