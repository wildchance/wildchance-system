"""Drone recon orchestration — fetch live gold + DXY + CBDR box, sweep, alert.

Network glue over gold.recon: pulls the live gold price, best-effort builds the
pre-London CBDR box (for the ±1/±1.5SD deviation factor), reads the live RBUSBIS
direction, runs the fused sweep, and alerts on an ARMED setup (best-effort marker
dedup so a cron stays quiet until the board actually changes).
"""

from __future__ import annotations

import os
from typing import Optional

from gold import recon as gr
from services import gold_scan  # Telegram sender

_STATE_DIR = os.environ.get("STATE_DIR", "state")
_MARKER = os.path.join(_STATE_DIR, "recon.state")


def _read_last() -> Optional[str]:
    try:
        with open(_MARKER) as f:
            return f.read().strip() or None
    except Exception:
        return None


def _write_last(sig: str) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_MARKER, "w") as f:
            f.write(sig)
    except Exception:
        pass


def _signature(sweep: dict) -> str:
    """Compact armed-state signature for dedup (side+zone+level+armed)."""
    return "|".join(f"{s['side']}:{s['zone']}:{s['cbdr_level']}:{int(s['armed'])}"
                    for s in sweep.get("setups", [])) or "none"


async def _live_box(window: str = "prelondon"):
    try:
        from services.cbdr_service import fetch_cbdr_window
        from cbdr.engine import build_cbdr
        w = await fetch_cbdr_window("XAU/USD", window=window)
        if w and w.get("high") is not None and w.get("low") is not None:
            return build_cbdr(w["high"], w["low"])
    except Exception:
        pass
    return None


async def _rbusbis_dir() -> Optional[str]:
    try:
        from services import fred_service as fred
        if fred.configured():
            usd = await fred.dollar_read()
            if usd:
                return usd["direction"]
    except Exception:
        pass
    return None


async def _b2b() -> Optional[dict]:
    """Live 4H b2b-bomber read (best-effort)."""
    try:
        from services.ohlc_service import fetch_ohlc_raw
        from gold.b2b import b2b_bomber
        ohlc = await fetch_ohlc_raw("XAU/USD", interval="4h", outputsize=30)
        if len(ohlc) >= 4:
            return b2b_bomber(ohlc)
    except Exception:
        pass
    return None


async def _warthog(interval: str = "1h") -> Optional[dict]:
    """Live HTF warthog (sweep + OTE) read (best-effort)."""
    try:
        from services.ohlc_service import fetch_ohlc_raw
        from gold.warthog import warthog as wh, to_ohlc
        raw = await fetch_ohlc_raw("XAU/USD", interval=interval, outputsize=80)
        if len(raw) >= 8:
            return wh(to_ohlc(raw))
    except Exception:
        pass
    return None


async def _radar(gold_price: float) -> Optional[dict]:
    """Live daily OB-radar scan (best-effort)."""
    try:
        from services.ohlc_service import fetch_ohlc
        from gold import radar as rd
        daily = await fetch_ohlc("XAU/USD", "1day", 60)
        if len(daily) >= 8:
            return rd.radar_scan(daily, gold_price)
    except Exception:
        pass
    return None


async def recon(dxy_price: Optional[float] = None, gold_price: Optional[float] = None,
                window: str = "prelondon", notify: bool = True,
                armed_only: bool = True, force: bool = False) -> dict:
    """Run the fused gold/DXY recon sweep and optionally alert on an armed board."""
    if gold_price is None:
        try:
            from utils.price_fetcher import get_forex_price
            gold_price = await get_forex_price("XAU/USD")
        except Exception:
            gold_price = None
    if gold_price is None:
        return {"error": "could not fetch XAU/USD price"}

    # live DXY level (genuine index only — the proxy is directional, not a level)
    if dxy_price is None:
        try:
            from services.dxy_service import latest_dxy
            d = await latest_dxy()
            if d and d.get("is_level"):
                dxy_price = d["price"]
        except Exception:
            pass
    box = await _live_box(window)
    rbusbis = await _rbusbis_dir()
    b2b = await _b2b()
    warthog = await _warthog()
    radar = await _radar(gold_price)
    sweep = gr.recon_sweep(gold_price, dxy_price=dxy_price, box=box,
                           rbusbis_dir=rbusbis, b2b=b2b, warthog=warthog, radar=radar)

    # Live retracement state on the board — SELL-the-OTE / scalp-the-bounce / LEAVE.
    retracement = None
    try:
        from services import retracement_service as rsvc
        retracement = await rsvc.live_read(gold_price=gold_price, box=box)
    except Exception:
        pass

    # Optimus Prime precision zones — locate the up/down-close OBs, arm on the reject
    # (the fix for the 4152/4163 miss). Armed zones join the board so they alert.
    optimus = None
    try:
        from services.ohlc_service import fetch_ohlc
        from gold import optimus as gop
        _obars = await fetch_ohlc("XAU/USD", "4h", 60)
        if len(_obars) >= 8:
            optimus = gop.optimus_scan(_obars, gold_price)
    except Exception:
        pass

    # Bumblebee — the intra-session sweep-continuity call (London/NY sweep → HTF OB).
    bumblebee = None
    try:
        import datetime as _dt
        from services.ohlc_service import fetch_hourly_raw
        from gold import bumblebee as gbb, radar as grd
        _h = await fetch_hourly_raw("XAU/USD", timezone="America/New_York", outputsize=60)
        if _h:
            _hb = [{"hour": int(str(x.get("datetime", ""))[11:13] or -1),
                    "open": float(x["open"]), "high": float(x["high"]),
                    "low": float(x["low"]), "close": float(x["close"])}
                   for x in _h if x.get("datetime")]
            _nh = int(_dt.datetime.utcnow().hour - 4) % 24
            _d = await fetch_ohlc("XAU/USD", "1day", 90)
            _hbias = grd.combine_htf(daily=grd.order_blocks(_d, timeframe="1D")
                                     if len(_d) >= 8 else []).get("htf_bias")
            # Venom AMD phase (folds ×manipulation conviction) + HTF-OB interaction gate
            _venom = None
            try:
                from gold import venom as gvenom
                _venom = gvenom.venom_read()
            except Exception:
                pass
            _ob_hit = bool((optimus and optimus.get("armed"))
                           or (radar and radar.get("active_retest")))
            bumblebee = gbb.bumblebee_scan(_hb, _nh, htf_bias=_hbias, venom=_venom,
                                           ob_interacted=_ob_hit)
    except Exception:
        pass

    sig = _signature(sweep)
    last = _read_last()
    changed = sig != last
    _optimus_armed = bool(optimus and optimus.get("armed"))
    _bee_call = bool(bumblebee and (bumblebee.get("outcome") or {}).get("signal") in ("BUY", "SELL"))
    should = force or ((sweep["armed"] or _optimus_armed or _bee_call or not armed_only) and changed)
    sent = False
    if notify and should:
        text = gr.format_recon(sweep)
        if retracement and retracement.get("actionable"):
            text += "\n\n" + retracement["display"]
        if _optimus_armed:
            from gold import optimus as gop
            text += "\n\n" + (gop.format_optimus(optimus) or "")
        if _bee_call:
            from gold import bumblebee as gbb
            text += "\n\n" + (gbb.format_bumblebee(bumblebee) or "")
        sent = await gold_scan._tg(text)
    _write_last(sig)
    from services import retracement_service as rsvc
    return {"sent": sent, "armed": sweep["armed"], "changed": changed,
            "had_box": box is not None, "rbusbis_dir": rbusbis, "sweep": sweep,
            "retracement": rsvc.summary(retracement) if retracement else None,
            "optimus": ({"armed": optimus.get("armed"), "next_zone": optimus.get("next_zone"),
                         "note": optimus.get("note")} if optimus else None),
            "bumblebee": ({"session": bumblebee.get("session"),
                           "outcome": bumblebee.get("outcome"),
                           "note": bumblebee.get("note")} if bumblebee else None)}
