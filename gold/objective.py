"""Campaign objective — the CBDR range-to-range target engine (pure).

The strategic objective is the NEXT range, not a single trade. On the HTF timeline
the named zones sit one 0.5-unit step apart — for the daily gold anchor that step is
≈ $248, i.e. the "~2,500 pips / $250 to the next CBDR range" leg you trade toward.

`campaign_objective(price)` reads where price sits on the HTF timeline, takes the
smaller-timeframe bias (discount → long, premium → short), and names the NEXT zone
in that direction as the objective plus the leg size. Every tactical entry (weekly
profile / intraday / intrasession / CRT / limits) is then judged by whether it
advances toward that objective via `advances(side, price)`.
"""

from __future__ import annotations

from typing import Optional

from gold import timeline as tl


def campaign_objective(price: float, zero: Optional[float] = None,
                       one: Optional[float] = None) -> dict:
    """The current HTF zone, the campaign direction, and the next-range objective."""
    loc = tl.locate(price, zero, one)
    bias = loc["smaller_tf_bias"]
    if bias == "long":
        objective = loc.get("nearest_above")
    elif bias == "short":
        objective = loc.get("nearest_below")
    else:
        objective = None

    leg = round(abs(objective["price"] - price), 3) if objective else None
    return {
        "price": loc["price"], "k": loc["k"], "region": loc["region"],
        "direction": bias,
        "current_zone": {"above": loc.get("nearest_above"), "below": loc.get("nearest_below")},
        "objective": objective,
        "leg_usd": leg,
        "note": (f"campaign {bias}: advance to {objective['zone']} @ {objective['price']} "
                 f"(~${leg} leg)" if objective else
                 "no campaign direction — HTF pivot, follow the trend"),
    }


def advances(side: str, price: float, zero: Optional[float] = None,
             one: Optional[float] = None) -> dict:
    """Does a proposed side advance the campaign toward the objective?

    'advances' when the side matches the campaign direction, 'retreats' when it
    opposes, 'neutral' at an HTF pivot. Carries the objective + leg for sizing/targeting.
    """
    obj = campaign_objective(price, zero, one)
    want = "long" if side.lower() in ("long", "buy") else "short"
    d = obj["direction"]
    if d == "neutral" or d is None:
        status = "neutral"
    else:
        status = "advances" if d == want else "retreats"
    return {"status": status, "campaign_direction": d,
            "objective": obj["objective"], "leg_usd": obj["leg_usd"],
            "reason": (f"{want} {status} the campaign (dir {d})")}
