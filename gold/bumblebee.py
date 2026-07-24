"""Bumblebee — intra-session sweep-and-continuity scalper (pure).

The operator's session model, codified for cheetah (250-pip) intraday scalps. All
hours are UTC-4 (broker time):

  • ASIAN 02:00–05:00 — the day's high/low forms here → the DAILY directional bias.
  • LONDON 00:00 → the 1H open candle sets the RANGE; 01:00 SWEEPS one side (grabs
    liquidity); then price runs toward the HTF order block. 02:00 confirms continuity.
  • NEW YORK 07:00 → the 1H open candle sets the range; 08:00 SWEEPS a side; 09:00 the
    trend commits IN CONFLUENCE with the HTF order block.

The engine: RANGE (anchor 1H high/low) → SWEEP (which side got taken + close-back) →
CONTINUITY (trade toward the HTF OB in the confirmed direction). A sweep of the HIGH
in a bearish HTF context = sell toward the OB below; a sweep of the LOW in a bullish
context = buy toward the OB above. No sweep, or a sweep against the HTF OB = WAIT.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

# Session phases in UTC-4: (range-anchor hour, sweep hour, continuity hour).
SESSIONS = {
    "london":  {"anchor": 0, "sweep": 1, "continuity": 2},
    "newyork": {"anchor": 7, "sweep": 8, "continuity": 9},
}
ASIAN_RANGE = (2, 5)      # UTC-4 window whose high/low sets the daily bias


def _hlc(bar):
    """(hour, o, h, l, c) — hour from dict 'hour'/'time' or tuple[0]."""
    if isinstance(bar, dict):
        h = bar.get("hour")
        if h is None and bar.get("time"):
            try:
                h = int(str(bar["time"])[11:13])
            except Exception:
                h = None
        return (h, float(bar["open"]), float(bar["high"]),
                float(bar["low"]), float(bar["close"]))
    return (bar[0], float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))


def anchor_range(bars: Sequence, anchor_hour: int) -> Optional[dict]:
    """The high/low of the 1H candle at the session anchor hour (most recent)."""
    for bar in reversed(list(bars)):
        hr, o, h, l, c = _hlc(bar)
        if hr == anchor_hour:
            return {"hour": hr, "high": round(h, 2), "low": round(l, 2),
                    "mid": round((h + l) / 2, 2)}
    return None


def detect_sweep(range_hi: float, range_lo: float, bars_after: Sequence) -> dict:
    """Which side of the range got swept after the anchor, and whether price closed
    back inside (the reject that confirms the grab)."""
    swept_high = swept_low = False
    reclaim_high = reclaim_low = False
    for bar in bars_after:
        _hr, o, h, l, c = _hlc(bar)
        if h > range_hi:
            swept_high = True
            reclaim_high = c < range_hi
        if l < range_lo:
            swept_low = True
            reclaim_low = c > range_lo
    side = None
    if swept_high and not swept_low:
        side = "high"
    elif swept_low and not swept_high:
        side = "low"
    elif swept_high and swept_low:
        side = "both"            # both sides taken — wait for the decisive one
    return {"side": side, "swept_high": swept_high, "swept_low": swept_low,
            "reclaim": reclaim_high if side == "high" else reclaim_low if side == "low" else False}


def continuity_call(sweep_side: Optional[str], htf_bias: Optional[str]) -> dict:
    """The trade the sweep sets up, gated by HTF-OB confluence. Sweep the HIGH →
    sell toward the OB below (needs a non-bullish HTF); sweep the LOW → buy toward
    the OB above (needs a non-bearish HTF)."""
    if sweep_side == "high":
        ok = htf_bias in ("short", "neutral", None)
        return {"signal": "SELL" if ok else "WAIT",
                "confluence": htf_bias == "short",
                "note": ("swept the session HIGH (buy-side grab) → sell toward the HTF "
                         "OB below" if ok else "swept high but HTF bullish — stand down")}
    if sweep_side == "low":
        ok = htf_bias in ("long", "neutral", None)
        return {"signal": "BUY" if ok else "WAIT",
                "confluence": htf_bias == "long",
                "note": ("swept the session LOW (sell-side grab) → buy toward the HTF "
                         "OB above" if ok else "swept low but HTF bearish — stand down")}
    if sweep_side == "both":
        return {"signal": "WAIT", "confluence": False,
                "note": "both sides swept — wait for the decisive close"}
    return {"signal": "WAIT", "confluence": False, "note": "no sweep yet — await the grab"}


def asian_bias(bars: Sequence) -> dict:
    """The daily directional bias from the Asian 02:00-05:00 range: whichever extreme
    prints LAST (high after low = up-day bias; low after high = down-day bias)."""
    lo_h, hi_h = ASIAN_RANGE
    win = [(_hlc(b)) for b in bars if _hlc(b)[0] is not None and lo_h <= _hlc(b)[0] <= hi_h]
    if not win:
        return {"bias": "neutral", "note": "no Asian-window bars"}
    hi = max(win, key=lambda r: r[2])
    lo = min(win, key=lambda r: r[3])
    hi_i, lo_i = win.index(hi), win.index(lo)
    bias = "long" if hi_i > lo_i else "short" if lo_i > hi_i else "neutral"
    return {"bias": bias, "asian_high": round(hi[2], 2), "asian_low": round(lo[3], 2),
            "note": f"Asian range {round(lo[3],2)}–{round(hi[2],2)} → daily bias {bias.upper()}"}


def phase_for_hour(hour: int) -> Optional[dict]:
    """Which session + phase the UTC-4 hour is in (range / sweep / continuity)."""
    for name, s in SESSIONS.items():
        if hour == s["anchor"]:
            return {"session": name, "phase": "range"}
        if hour == s["sweep"]:
            return {"session": name, "phase": "sweep"}
        if hour == s["continuity"]:
            return {"session": name, "phase": "continuity"}
    if ASIAN_RANGE[0] <= hour <= ASIAN_RANGE[1]:
        return {"session": "asian", "phase": "range"}
    return None


def bumblebee_scan(bars_1h: Sequence, now_hour: int, htf_bias: Optional[str] = None,
                   session: Optional[str] = None, ob_target: Optional[float] = None) -> dict:
    """Full Bumblebee read for the active (or given) session: the anchor range, the
    sweep, and the continuity call toward the HTF OB. Feed hour-tagged 1H bars."""
    sess = session
    if sess is None:
        ph = phase_for_hour(now_hour)
        sess = (ph or {}).get("session")
    ab = asian_bias(bars_1h)
    bias = htf_bias or (ab["bias"] if ab["bias"] != "neutral" else None)
    if sess in (None, "asian"):
        return {"session": sess or "none", "asian_bias": ab, "htf_bias": htf_bias,
                "phase": "range" if sess == "asian" else "off-session",
                "note": "Asian range sets the daily bias; wait for London/NY anchor"}
    cfg = SESSIONS[sess]
    rng = anchor_range(bars_1h, cfg["anchor"])
    if not rng:
        return {"session": sess, "asian_bias": ab, "htf_bias": bias,
                "note": f"no {sess} anchor ({cfg['anchor']}:00) candle yet"}
    # The sweep is the SWEEP-hour candle only — the continuity hour breaks the range
    # in the trade direction (continuation), which must not count as a second sweep.
    sweep_bars = [b for b in bars_1h if _hlc(b)[0] == cfg["sweep"]]
    if not sweep_bars:      # fall back to the bars between anchor and continuity
        sweep_bars = [b for b in bars_1h if _hlc(b)[0] is not None
                      and cfg["anchor"] < _hlc(b)[0] < cfg["continuity"]]
    sweep = detect_sweep(rng["high"], rng["low"], sweep_bars)
    call = continuity_call(sweep["side"], bias)
    # target toward the HTF OB (opposite the sweep) — a cheetah scalp by default
    target = ob_target
    return {
        "session": sess, "phase": (phase_for_hour(now_hour) or {}).get("phase"),
        "range": rng, "sweep": sweep, "htf_bias": bias, "asian_bias": ab,
        "continuity": call, "ob_target": target,
        "scalp": "cheetah (≥250 pips) intra-session toward the HTF OB",
        "note": (f"{sess.upper()} {rng['low']}–{rng['high']}: "
                 + (f"swept {sweep['side']} → {call['signal']}"
                    f"{' ✅HTF' if call['confluence'] else ''}"
                    if sweep["side"] else "awaiting the sweep")),
    }


def format_bumblebee(scan: dict) -> Optional[str]:
    """Telegram line when Bumblebee has a confluent continuity call."""
    call = scan.get("continuity") or {}
    if call.get("signal") not in ("BUY", "SELL"):
        return None
    icon = "🟢" if call["signal"] == "BUY" else "🔴"
    rng = scan.get("range") or {}
    tag = " ✅HTF" if call.get("confluence") else ""
    return (f"🐝 *BUMBLEBEE — {scan['session'].upper()}*  {icon} {call['signal']}{tag}\n"
            f"   range {rng.get('low')}–{rng.get('high')}  ·  swept {scan['sweep']['side']}\n"
            f"   {call['note']}"
            + (f"  → OB {scan['ob_target']}" if scan.get("ob_target") else ""))
