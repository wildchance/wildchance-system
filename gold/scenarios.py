"""Four-scenario labeling (B13) — classify the live tape into the Kingdom's four
execution scenarios, each with its lean:

  • liquidity_sweep      — price tags a ±SD extreme, grabs the stops, and rejects
    back inside → REVERSAL setup (fade the sweep).
  • direct_expansion     — price breaks a box edge and CLOSES beyond with momentum,
    no deep pullback → CONTINUATION (join the expansion).
  • deep_institutional_hunt — price drives well past the extreme (≥±2SD / into a deep
    HTF OB) hunting liquidity before it turns → WAIT for the deeper reversal.
  • dead_cat_bounce      — a weak counter-trend bounce against a locked/aligned bias
    → SKIP / fade the bounce (don't chase).

Scores are geometric + regime-conditioned and normalised to a distribution. Pure:
feed the CBDR box, live price, the fused HTF bias, and recent bars.
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


def classify_scenario(box, price: float, htf_bias: Optional[str] = None,
                      bars: Optional[Sequence] = None) -> dict:
    """Return the four-scenario probability distribution + the dominant scenario."""
    if box is None or price is None:
        return {"error": "need CBDR box + price"}
    price = float(price)
    lv = getattr(box, "levels", {}) or {}
    mid = box.mid
    up = price >= mid
    # nearest relevant extremes
    sd1 = lv.get("+1SD" if up else "-1SD")
    sd15 = lv.get("+1.5SD" if up else "-1.5SD")
    sd2 = lv.get("+2SD" if up else "-2SD")
    edge = box.high if up else box.low

    beyond_1 = sd1 is not None and (price >= sd1 if up else price <= sd1)
    beyond_15 = sd15 is not None and (price >= sd15 if up else price <= sd15)
    beyond_2 = sd2 is not None and (price >= sd2 if up else price <= sd2)
    broke_edge = (price > box.high) if up else (price < box.low)

    # rejection / reclaim from the recent bars
    reclaim = False
    momentum = 0.0
    if bars:
        o, h, l, c = _ohlc(bars[-1])
        rng = max(h - l, 1e-9)
        momentum = abs(c - o) / rng                     # body fraction = follow-through
        if up:
            reclaim = h >= (sd1 or edge) and c < (sd1 or edge)   # swept up, closed back
        else:
            reclaim = l <= (sd1 or edge) and c > (sd1 or edge)

    scores = {"liquidity_sweep": 0.0, "direct_expansion": 0.0,
              "deep_institutional_hunt": 0.0, "dead_cat_bounce": 0.0}

    # liquidity sweep: at an extreme + rejected back inside
    if beyond_1 and reclaim:
        scores["liquidity_sweep"] += 1.2 + (0.5 if beyond_15 else 0.0)
    # direct expansion: broke the edge, momentum, NOT rejected
    if broke_edge and momentum > 0.5 and not reclaim:
        scores["direct_expansion"] += 1.0 + momentum
    # deep hunt: driven past 2SD (with or without reclaim yet)
    if beyond_2:
        scores["deep_institutional_hunt"] += 1.3
    elif beyond_15 and not reclaim:
        scores["deep_institutional_hunt"] += 0.6
    # dead cat: price pushing AGAINST a directional htf bias, weak momentum
    if htf_bias in ("long", "short"):
        against = (htf_bias == "short" and up) or (htf_bias == "long" and not up)
        if against and momentum < 0.5:
            scores["dead_cat_bounce"] += 1.0
    # baseline so it's never all-zero
    if sum(scores.values()) == 0:
        scores["direct_expansion" if broke_edge else "dead_cat_bounce"] += 0.4

    probs = _norm(scores)
    top = max(probs, key=probs.get)
    leans = {
        "liquidity_sweep": ("short" if up else "long", "fade the sweep — reversal off the extreme"),
        "direct_expansion": ("long" if up else "short", "join the expansion — continuation"),
        "deep_institutional_hunt": ("wait", "let the deep hunt complete, then fade the exhaustion"),
        "dead_cat_bounce": ("short" if up else "long", "skip/fade — weak counter-trend bounce"),
    }
    lean, action = leans[top]
    return {
        "price": round(price, 2), "at_extreme": beyond_1, "beyond_2sd": beyond_2,
        "broke_edge": broke_edge, "reclaim": reclaim,
        "probabilities": probs, "scenario": top, "lean": lean,
        "note": f"{top.replace('_',' ')} ({int(probs[top]*100)}%) → {action}",
    }
