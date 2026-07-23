"""Trap detection (B10) — a conditional-probability read over a level sweep.

Wraps the deterministic sweep-and-reject mechanic (gold.rejection) in a net
probability distribution across the four outcomes a level test resolves into:

  • clean_breakout — swept the level AND closed beyond it with body follow-through
    (real continuation).
  • bull_trap      — swept ABOVE the level (grabbed buy-side liquidity) then closed
    back BELOW → longs trapped, reversal down.
  • bear_trap      — swept BELOW then closed back ABOVE → shorts trapped, reversal up.
  • capitulation   — an outsized-range close beyond the level with an exhaustion wick
    (climactic, mean-reversion risk).

The scores are geometric (displacement, close location, wick/body ratio, follow-
through) normalised to a distribution that sums to 1 — a probabilistic layer, not a
binary trigger. Feed recent (o,h,l,c) / (t,o,h,l,c) / dict bars oldest-first.
"""

from __future__ import annotations

from typing import Optional, Sequence


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
    if len(bar) >= 5:
        return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))
    return (float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]))


def _norm(scores: dict) -> dict:
    tot = sum(scores.values())
    if tot <= 0:
        n = len(scores)
        return {k: round(1.0 / n, 3) for k in scores}
    return {k: round(v / tot, 3) for k, v in scores.items()}


def trap_probabilities(bars: Sequence, level: float, lookback: int = 3) -> dict:
    """Net probability distribution across breakout / bull-trap / bear-trap /
    capitulation for the most recent test of ``level``."""
    if not bars or level is None:
        return {"error": "need bars + level"}
    window = [ _ohlc(b) for b in list(bars)[-lookback:] ]
    o, h, l, c = window[-1]                       # the decisive (last) bar
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    swept_above = any(bh > level for (_o, bh, _l, _c) in window)
    swept_below = any(bl < level for (_o, _h, bl, _c) in window)
    closed_above = c > level
    closed_below = c < level

    # geometric evidence
    disp_above = max(0.0, h - level) / rng        # how far the wick poked above
    disp_below = max(0.0, level - l) / rng
    close_dist = abs(c - level) / rng             # conviction of the close beyond
    body_frac = body / rng                        # follow-through vs wick
    upper_wick = (h - max(o, c)) / rng
    lower_wick = (min(o, c) - l) / rng

    scores = {"clean_breakout": 0.0, "bull_trap": 0.0,
              "bear_trap": 0.0, "capitulation": 0.0}

    if closed_above:
        # broke up: clean if body-led, bull_trap risk was the sweep that FAILED
        scores["clean_breakout"] += close_dist + body_frac
        if swept_below:                            # dipped under first then reclaimed
            scores["bear_trap"] += disp_below + lower_wick
    if closed_below:
        scores["clean_breakout"] += close_dist + body_frac
        if swept_above:                            # poked over then rejected
            scores["bull_trap"] += disp_above + upper_wick
    if swept_above and closed_below:
        scores["bull_trap"] += disp_above + upper_wick + 0.3
    if swept_below and closed_above:
        scores["bear_trap"] += disp_below + lower_wick + 0.3
    # capitulation: large range + long wick against the close = exhaustion
    if body_frac < 0.45 and (upper_wick > 0.4 or lower_wick > 0.4):
        scores["capitulation"] += max(upper_wick, lower_wick) + (1.0 - body_frac)

    probs = _norm(scores)
    top = max(probs, key=probs.get)
    bias = {"clean_breakout": ("long" if closed_above else "short"),
            "bull_trap": "short", "bear_trap": "long",
            "capitulation": ("short" if upper_wick > lower_wick else "long")}[top]
    return {
        "level": round(level, 2), "close": round(c, 2),
        "swept_above": swept_above, "swept_below": swept_below,
        "probabilities": probs, "most_likely": top, "implied_bias": bias,
        "note": (f"{top.replace('_',' ')} most likely ({int(probs[top]*100)}%) → "
                 f"{bias.upper()} lean at {round(level,2)}"),
    }


def trap_from_sweep(bars: Sequence, level: float, side: str, lookback: int = 3) -> dict:
    """Combine the probability read with the confirmed sweep-reject trigger — the
    highest-conviction trap is one the rejection engine also confirms."""
    from gold.rejection import sweep_reject
    probs = trap_probabilities(bars, level, lookback)
    rej = sweep_reject(bars, level, side, lookback=lookback)
    probs["sweep_reject_confirmed"] = bool(rej)
    if rej:
        probs["reject_entry"] = rej.get("entry")
        probs["reject_stop"] = rej.get("stop")
    return probs
