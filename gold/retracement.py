"""Live retracement read — which of the THREE states are we in right now? (pure).

Selling a retracement inside a bullish leg (or buying one inside a bearish leg)
is the classic way to get run over. This module makes the system's decision
VISIBLE at a glance by classifying the live tape into exactly one of three states:

  • SELL-the-OTE   — a DOWN impulse retraced UP into the OTE band (62–79%), swept a
                     high and REJECTED (closed back below), and the HTF bias is not
                     bullish. Sell the retracement top WITH the trend. Full size.
  • scalp-the-bounce — price swept a LOW and reversed UP (a −1SD/−1.5SD CBDR extreme
                     and/or a fresh buy OB, confirmed by a close back above). A small
                     MEAN-REVERSION scalp against a bearish trend — never a trend long,
                     never conviction-scaled, tight and quick.
  • LEAVE          — mid-retracement (roughly 30–50%), no sweep, no rejection, no
                     confluence. The dangerous middle. Stand down.

The rule the system enforces: it only SELLS the retracement once the OTE top has
swept + rejected (never mid-move), it only SCALPS the bounce small and range-fade
(never flips to a trend long while the DXY lock holds), and it LEAVES everything
in between. Built on gold.warthog (impulse leg / OTE / sweep) and
gold.rejection.sweep_reject (the close-back-inside trigger).
"""

from __future__ import annotations

from typing import Optional, Sequence

from gold.warthog import _impulse_leg, detect_sweep, warthog, to_ohlc
from gold.rejection import sweep_reject

# The OTE retracement band (fraction of the impulse leg given back).
OTE_LO = 0.62
OTE_HI = 0.79
# The "dangerous middle" — no-trade retracement band.
MID_LO = 0.30
MID_HI = 0.50


def _at_discount_extreme(box, price: float) -> Optional[str]:
    """The CBDR −1SD / −1.5SD discount extreme a bounce-scalp wants, or None."""
    if box is None:
        return None
    lv = getattr(box, "levels", {}) or {}
    e_deep, e_min = lv.get("-1.5SD"), lv.get("-1SD")
    if e_deep is not None and price <= e_deep:
        return "-1.5SD"
    if e_min is not None and price <= e_min:
        return "-1SD"
    return None


def retracement_state(bars: Sequence, price: Optional[float] = None,
                      htf_bias: Optional[str] = None, box=None,
                      dxy_unlocked: bool = False,
                      buy_ob: Optional[dict] = None) -> dict:
    """Classify the live retracement into SELL_OTE / SCALP_BOUNCE / LEAVE.

    ``bars`` are HTF (o,h,l,c) tuples oldest-first (or fetch dicts — normalised).
    ``htf_bias`` is the fused OB bias ("long"/"short"/"neutral"); ``box`` an optional
    cbdr.engine.CBDR for the deviation extreme; ``dxy_unlocked`` True only once the
    DXY flip has unlocked trend longs (guards the sell against a real bull trend);
    ``buy_ob`` an optional fresh demand OB the bounce is reclaiming.
    """
    obars = to_ohlc(bars)
    if len(obars) < 8:
        return {"state": "LEAVE", "actionable": False,
                "reason": "need >=8 HTF candles for an impulse leg"}

    # sweep_reject expects (t,o,h,l,c) rows / dicts — re-wrap the normalised tuples.
    rbars = [(None, o, h, l, c) for (o, h, l, c) in obars]

    trend, leg_low, leg_high = _impulse_leg(obars)
    rng = leg_high - leg_low
    if price is None:
        price = obars[-1][3]
    price = float(price)
    if rng <= 0:
        return {"state": "LEAVE", "actionable": False, "reason": "degenerate leg"}

    # Retracement fraction: how far the impulse has been given back (0 = at the
    # extreme it ran to, 1 = fully retraced back to its origin).
    if trend == "bearish":
        retr = (price - leg_low) / rng          # up-retrace of a down leg
    elif trend == "bullish":
        retr = (leg_high - price) / rng          # down-retrace of an up leg
    else:
        retr = 0.0
    retr = round(max(0.0, min(1.5, retr)), 3)
    in_ote = OTE_LO <= retr <= OTE_HI
    in_mid = MID_LO <= retr <= MID_HI

    sweep = detect_sweep(obars)
    swept_high = bool(sweep and sweep["type"] == "high" and sweep.get("reclaim"))
    swept_low = bool(sweep and sweep["type"] == "low" and sweep.get("reclaim"))

    # HTF bias is "not bullish" when it's short/neutral/unknown, OR it's long but the
    # DXY flip has NOT unlocked trend longs (so gold still isn't structurally bullish).
    htf_not_bullish = htf_bias in (None, "short", "neutral") or not dxy_unlocked

    # --- SELL-the-OTE — sell the retracement top WITH a bearish trend --------------
    if trend == "bearish" and in_ote and swept_high and htf_not_bullish:
        rej = sweep_reject(rbars, sweep["level"], "short", lookback=3)
        card = warthog(obars, side="short")
        entry = (rej or {}).get("entry") or card.get("entry")
        stop = (rej or {}).get("stop") or card.get("stop")
        return {
            "state": "SELL_OTE", "label": "SELL-the-OTE", "actionable": True,
            "signal": "SHORT", "size": "full", "trade_type": "swing",
            "trend": trend, "retracement": retr, "in_ote": True,
            "sweep": sweep, "entry": entry, "stop": stop,
            "targets": card.get("targets"), "liquidity_targets": card.get("liquidity_targets"),
            "htf_bias": htf_bias,
            "reason": (f"down-leg retraced {int(retr*100)}% into OTE, swept the high "
                       f"{sweep['level']} and rejected — sell the top with the trend"),
        }

    # --- scalp-the-bounce — small mean-reversion off a swept low ------------------
    dev = _at_discount_extreme(box, price)
    if swept_low and (dev is not None or buy_ob is not None):
        rej = sweep_reject(rbars, sweep["level"], "long", lookback=3)
        if rej:
            conf = []
            if dev:
                conf.append(f"CBDR {dev}")
            if buy_ob:
                conf.append(f"buy OB {buy_ob.get('zone', buy_ob)}")
            return {
                "state": "SCALP_BOUNCE", "label": "scalp-the-bounce", "actionable": True,
                "signal": "LONG", "size": "scalp", "trade_type": "sd_fade",
                "range_fade_only": True, "conviction_scaled": False,
                "trend": trend, "retracement": retr,
                "sweep": sweep, "entry": rej["entry"], "stop": rej["stop"],
                "confluence": conf, "htf_bias": htf_bias,
                "warning": ("range-fade scalp ONLY — do NOT hold as a trend long "
                            "while the DXY lock is on"),
                "reason": (f"swept the low {sweep['level']} and reclaimed at "
                           f"{rej['close']}" + (f" ({', '.join(conf)})" if conf else "")
                           + " — scalp the bounce small"),
            }

    # --- LEAVE — the dangerous middle ---------------------------------------------
    if in_mid:
        why = f"mid-retracement ({int(retr*100)}%) — no OTE sweep-reject, no swept-low bounce"
    elif in_ote and trend == "bearish" and not swept_high:
        why = f"in the OTE ({int(retr*100)}%) but the high has not swept+rejected yet — wait"
    else:
        why = (f"{int(retr*100)}% retrace, no confluence — "
               "not at an OTE top or a swept-low extreme")
    return {"state": "LEAVE", "label": "LEAVE", "actionable": False,
            "signal": None, "trend": trend, "retracement": retr, "in_ote": in_ote,
            "sweep": sweep, "htf_bias": htf_bias, "reason": why}


def format_retracement(read: dict) -> str:
    """One-glance Telegram/console line for the current retracement state."""
    icon = {"SELL_OTE": "🔻", "SCALP_BOUNCE": "🟢", "LEAVE": "⏸️"}.get(read["state"], "❔")
    head = f"{icon} *{read.get('label', read['state'])}*"
    if read["state"] == "SELL_OTE":
        return (f"{head}  ({read['trend']}, {int(read['retracement']*100)}% OTE)\n"
                f"   SHORT entry `{read.get('entry')}`  SL `{read.get('stop')}`  ·  full size\n"
                f"   {read['reason']}")
    if read["state"] == "SCALP_BOUNCE":
        return (f"{head}  (swept low, {int(read['retracement']*100)}% retr)\n"
                f"   LONG entry `{read.get('entry')}`  SL `{read.get('stop')}`  ·  scalp only\n"
                f"   ⚠️ {read.get('warning')}\n   {read['reason']}")
    return f"{head}  ·  {read['reason']}"
