"""Signal builder + emitter — auto (CBDR zones) or manual, gated by prop risk.

Builds a trade signal with Entry / Stop / TP1·TP2·TP3 (R multiples), runs it
through the prop-firm risk gate, renders a text card + QR reference, and emits to
Telegram and WhatsApp. Pure-ish: network only in fetch/emit; the math is testable.

Two builders:
  • build_auto(symbol, side, window, ...)  — TP zones computed from the CBDR box
    of `window` (cbdr | prelondon | trade1..6) via the setups engine
  • build_manual(symbol, side, entry, sl, tps)  — you supply every price
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import List, Optional

from cbdr.engine import build_cbdr
from setups.engine import build_setup
from services.cbdr_service import fetch_cbdr_window
from services.ohlc_service import fetch_ohlc, to_hlc
from services.news_guard import news_flag
from services import mmm_service
from indicators.atr import atr as compute_atr, is_spike
from utils.price_fetcher import get_forex_price
from propfirm.engine import evaluate_trade, max_lot, risk_limits


def _r(entry: float, stop: float, target: float) -> Optional[float]:
    risk = abs(entry - stop)
    return round(abs(target - entry) / risk, 2) if risk > 0 else None


def _assemble(symbol: str, side: str, entry: float, stop: float,
              tps: List[float], balance: float, tier: str,
              mode: str, extra: Optional[dict] = None) -> dict:
    targets = [{"name": f"TP{i}", "price": round(p, 5), "r": _r(entry, stop, p)}
               for i, p in enumerate(tps, start=1)]
    lim = risk_limits(balance, tier)
    risk_usd = lim["daily_loss_soft"]                    # one conservative risk unit
    gate = evaluate_trade(balance, risk_usd, day_pnl_usd=0.0, open_risk_usd=0.0, tier=tier)
    sig = {
        "symbol": symbol, "side": side.lower(), "mode": mode,
        "entry": round(entry, 5), "stop_loss": round(stop, 5),
        "targets": targets,
        "risk_per_unit": round(abs(entry - stop), 5),
        "sizing": {"balance": balance, "tier": lim["tier"],
                   "max_lot": max_lot(balance), "risk_usd": risk_usd},
        "gate": gate,
    }
    if extra:
        sig.update(extra)
    return sig


async def build_auto(symbol: str, side: str, window: str = "cbdr",
                     mode: str = "continuation", balance: float = 2500.0,
                     tier: str = "6", entry: Optional[float] = None,
                     atr_period: int = 14, atr_mult: float = 1.5) -> dict:
    win = await fetch_cbdr_window(symbol, window)
    if not win:
        return {"error": f"no '{window}' box for {symbol}"}
    box = build_cbdr(win["high"], win["low"])
    if entry is None:
        try:
            entry = await get_forex_price(symbol)
        except Exception:
            entry = None
    if entry is None:
        entry = box.mid

    # Volatility overlay: ATR-floored stop + spike filter from daily OHLC.
    atr_val = None
    spike = None
    bars = await fetch_ohlc(symbol, "1day", max(atr_period + 5, 30))
    if len(bars) >= atr_period + 1:
        hlc = to_hlc(bars)
        atr_val = compute_atr(hlc, atr_period)
        spike = is_spike(hlc[-1], atr_val)

    plan = build_setup(box, side, entry, mode,
                       atr=atr_val, atr_mult=atr_mult, spike=spike)
    tps = [t["price"] for t in plan["targets"]]
    extra = {"source": "auto", "window": win["window"],
             "window_label": win["label"], "session": win["session"]}
    if plan.get("atr"):
        extra["atr"] = plan["atr"]
    if plan.get("spike") is not None:
        extra["spike"] = plan["spike"]
    if plan.get("entry_warning"):
        extra["entry_warning"] = plan["entry_warning"]
    news = await news_flag(_dt.date.today(), symbol)
    if news:
        extra["news_warning"] = news
    mmm_conf = mmm_service.confluence(
        await mmm_service.read_with_daily(symbol, bars), side)
    if mmm_conf.get("status") != "none":
        extra["mmm_confluence"] = mmm_conf
    return _assemble(symbol, side, entry, plan["stop_loss"], tps, balance, tier, mode,
                     extra=extra)


def build_manual(symbol: str, side: str, entry: float, stop: float,
                 tps: List[float], balance: float = 2500.0, tier: str = "6") -> dict:
    return _assemble(symbol, side, entry, stop, tps, balance, tier, "manual",
                     extra={"source": "manual"})


def format_card(sig: dict) -> str:
    if sig.get("error"):
        return f"⚠️ {sig['error']}"
    arrow = "🟢 BUY" if sig["side"] == "long" else "🔴 SELL"
    lines = [
        f"📡 *Wildchance Signal* — {sig['symbol']}  {arrow}",
        f"_{sig.get('source','')}{('/' + sig['window_label']) if sig.get('window_label') else ''} · {sig['mode']}_",
    ]
    if sig.get("entry_warning"):
        lines += ["", sig["entry_warning"]]
    if sig.get("news_warning"):
        lines += ["", sig["news_warning"]]
    lines += [
        "",
        f"Entry:  `{sig['entry']}`",
        f"Stop:   `{sig['stop_loss']}`",
    ]
    for t in sig["targets"]:
        r = f"  (R {t['r']})" if t["r"] is not None else ""
        lines.append(f"{t['name']}:    `{t['price']}`{r}")
    atr = sig.get("atr")
    if atr:
        floored = "  (stop floored to ATR)" if atr.get("stop_floored_to_atr") else ""
        lines.append(f"ATR({atr['atr']}) ×{atr['atr_mult']}{floored}")
    mc = sig.get("mmm_confluence")
    if mc and mc.get("status") in ("confirms", "diverges", "neutral"):
        icon = {"confirms": "✅", "diverges": "⚠️", "neutral": "➖"}[mc["status"]]
        lines.append(f"MMM: {icon} {mc['status']} ({mc.get('taylor_day') or '—'})")
    ef = sig.get("edgefinder")
    if ef and ef.get("status"):
        icon = {"confirms": "✅", "diverges": "⚠️", "neutral": "➖"}.get(ef["status"], "➖")
        lines.append(f"Edge: {icon} {ef.get('bias')} ({ef.get('score'):+d})")
    s = sig["sizing"]
    lines += ["", f"Lot ≤ {s['max_lot']}  ·  tier {s['tier']}  ·  bal ${s['balance']}"]
    g = sig["gate"]
    lines.append(("✅ GATE: " + g["reason"]) if g["allow"] else ("⛔ GATE: " + g["reason"]))
    return "\n".join(lines)


def qr_payload(sig: dict) -> str:
    """Compact reference encoded into the QR (like the SimpleSignals card)."""
    tps = ",".join(str(t["price"]) for t in sig.get("targets", []))
    return (f"WILDCHANCE|{sig.get('symbol')}|{sig.get('side','').upper()}|"
            f"E:{sig.get('entry')}|SL:{sig.get('stop_loss')}|TP:{tps}")


def make_qr(text: str) -> Optional[bytes]:
    """PNG bytes of a QR for `text`, or None if the qrcode lib isn't installed."""
    try:
        import qrcode
        from io import BytesIO
        img = qrcode.make(text)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None
