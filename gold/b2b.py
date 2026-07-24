"""4H "b2b bomber" — the 1-5-9 sweep + back-to-back continuation (pure, stdlib).

The observed 4-hour pattern (OANDA, UTC-4): when a 4H candle SWEEPS the prior
candle's liquidity (its low below the prior low, or its high above the prior high),
the move then CONTINUES with the next two 4H candles — an 8-hour "back-to-back"
run. Labelled 1-5-9 on the fractal: candle 1 is the sweep, candles 5 and 9 (the
next two 4H closes) are the continuation confirmation.

  sweep LOW  (grab sell-side liquidity) → bullish continuation → LONG confluence
  sweep HIGH (grab buy-side liquidity)  → bearish continuation → SHORT confluence

The setup completes after the two continuation candles close (8h), giving a precise
swing-entry confluence. It is anchored to the new-trading-day boundaries the user
runs — Asian 00:00 close and New York 14:00 new-CBDR (UTC-4) — flagged when the
sweep candle opens at one of those session anchors.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional, Sequence

# Session anchors (UTC-4): Asian close / start-of-day and the NY new-CBDR open.
ANCHOR_HOURS = (0, 14)
ANCHOR_TZ_OFFSET = -4        # OANDA feed on the user's chart


def _ohlc(bar):
    """Accept a dict {open,high,low,close,(time)} or a [ts,o,h,l,c] sequence."""
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]),
                float(bar["close"]), bar.get("time") or bar.get("datetime"))
    return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]),
            bar[0] if len(bar) > 0 else None)


def _hour_utc_minus4(ts) -> Optional[int]:
    """Hour-of-day of a timestamp shifted to UTC-4 (the chart's timezone)."""
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is None:                     # assume the feed is already UTC
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        dt = dt.astimezone(_dt.timezone(_dt.timedelta(hours=ANCHOR_TZ_OFFSET)))
        return dt.hour
    except Exception:
        return None


def b2b_bomber(bars: Sequence, anchor_hours: Sequence[int] = ANCHOR_HOURS) -> dict:
    """Detect the most recent completed 4H sweep + back-to-back continuation.

    ``bars`` are 4H OHLC candles OLDEST→NEWEST (the last one is the latest CLOSED
    candle). Needs at least 4: a reference, the sweep, and the two continuation
    candles. Returns a LONG/SHORT confluence card or {"signal": "NONE"}.
    """
    if len(bars) < 4:
        return {"signal": "NONE", "pattern": "b2b_bomber",
                "reason": "need >=4 completed 4H candles"}
    o0, h0, l0, c0, _t0 = _ohlc(bars[-4])          # reference (liquidity)
    o1, h1, l1, c1, t1 = _ohlc(bars[-3])           # candle 1 — the sweep
    o2, h2, l2, c2, _t2 = _ohlc(bars[-2])          # candle 5 — continuation
    o3, h3, l3, c3, _t3 = _ohlc(bars[-1])          # candle 9 — continuation

    swept_low = l1 < l0
    swept_high = h1 > h0
    # bullish: swept the low then two back-to-back higher closes
    bull = swept_low and (c2 > c1) and (c3 > c2)
    # bearish: swept the high then two back-to-back lower closes
    bear = swept_high and (c2 < c1) and (c3 < c2)

    if not (bull or bear):
        why = "sweep without back-to-back continuation" if (swept_low or swept_high) \
              else "no liquidity sweep on candle 1"
        return {"signal": "NONE", "pattern": "b2b_bomber", "reason": why,
                "swept": "low" if swept_low else "high" if swept_high else None}

    if bull:
        signal, swept, sweep_level = "LONG", "low", l1
        invalidation = min(l1, l0)                 # below the sweep = pattern void
        target_ref = max(h1, h2, h3)
    else:
        signal, swept, sweep_level = "SHORT", "high", h1
        invalidation = max(h1, h0)
        target_ref = min(l1, l2, l3)

    hour = _hour_utc_minus4(t1)
    anchored = hour in tuple(anchor_hours) if hour is not None else None
    session = ("asia_00" if hour == 0 else "ny_14" if hour == 14 else None)

    # Actionable swing card — enter at the sweep candle's close (candle 1), stop just
    # beyond the swept extreme (the invalidation), target the continuation extreme;
    # the ride is the two continuation 4H closes ≈ 8 hours.
    _buf = 1.5
    entry = round(c1, 2)
    stop = round((invalidation - _buf) if signal == "LONG" else (invalidation + _buf), 2)
    target = round(target_ref, 2)
    risk = abs(entry - stop) or 1e-9
    rr = round(abs(target - entry) / risk, 2)

    return {
        "signal": signal, "pattern": "b2b_bomber", "swept": swept,
        "sweep_candle_hour_utc4": hour, "anchored": anchored, "anchor_session": session,
        "sweep_level": round(sweep_level, 2), "invalidation": round(invalidation, 2),
        "entry": entry, "stop": stop, "target": target, "rr": rr,
        "horizon_hours": 8, "trade_type": "swing",
        "continuation_closes": [round(c2, 2), round(c3, 2)],
        "target_ref": round(target_ref, 2),
        "note": (f"4H b2b bomber {signal}: candle 1 swept the {swept}, then 8h "
                 f"back-to-back continuation"
                 + (f" (anchored {session})" if anchored else "")
                 + f"; invalidation {round(invalidation, 2)}"),
    }


def format_b2b(read: dict) -> Optional[str]:
    """Telegram line for a fired b2b bomber, else None."""
    if not read or read.get("signal") not in ("LONG", "SHORT"):
        return None
    arrow = "🟢" if read["signal"] == "LONG" else "🔴"
    tag = f" · {read['anchor_session']}" if read.get("anchored") else ""
    return (f"💣 *4H B2B BOMBER — {read['signal']}*{tag}  (~8h swing)\n"
            f"{arrow} swept the {read['swept']} then 8h back-to-back continuation\n"
            f"   entry `{read.get('entry')}`  SL `{read.get('stop')}`  TP `{read.get('target')}`"
            f"  ({read.get('rr')}R)")
