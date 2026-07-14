"""TPO / Market Profile — time-at-price value area (POC / VAH / VAL) from OHLC.

Spot gold has NO real volume (FX is decentralised — no consolidated tape), so a
true volume profile can't be built from the feed. The industry-standard FX
workaround is a **TPO (Time Price Opportunity) profile**: instead of volume, count
how much TIME price spent at each level — one tally per bar that trades through a
price bin. That yields a real Point of Control and 70% value area from OHLC alone.

Used to CONFIRM a session breakout: a break is only meaningful if price is leaving
BALANCE — i.e. it clears the value area (not just the opening range), and the OR
itself wasn't sitting on the POC (which would mean the range is the fair-value
chop, not a coil ready to expand). When the MT5 bridge is live, feed real tick
volume into flow.footprint for a volume-weighted POC — this stays the fallback.

  tpo_profile(bars, bin_size)      time-at-price histogram + POC
  value_area(profile, coverage)    VAH/VAL enclosing ``coverage`` of the TPOs
  breakout_confirmed(...)          the leaving-balance gate for a session break
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

Bar = Tuple[object, float, float, float, float]


def tpo_profile(bars: Sequence[Bar], bin_size: float = 0.5) -> Optional[dict]:
    """Time-at-price histogram: each bar tallies +1 to every price bin it spans.

    ``bin_size`` is the price granularity (e.g. $0.5 for gold). Returns bins keyed
    by their lower edge → tally, the POC (most-tallied bin's centre), the total
    TPO count, and the price extent. None if bars/bin_size are unusable.
    """
    if not bars or bin_size <= 0:
        return None
    counts: Dict[int, int] = {}
    for b in bars:
        lo, hi = b[3], b[2]
        if hi < lo:
            lo, hi = hi, lo
        i0 = int(lo // bin_size)
        i1 = int(hi // bin_size)
        for i in range(i0, i1 + 1):
            counts[i] = counts.get(i, 0) + 1
    if not counts:
        return None
    total = sum(counts.values())
    # POC = most-tallied bin; on a tie pick the bin nearest the profile's centre
    # (the classic Market-Profile rule — the middle of balance, not an edge).
    mid_i = (min(counts) + max(counts)) / 2.0
    poc_i = max(counts, key=lambda i: (counts[i], -abs(i - mid_i)))
    poc = round((poc_i + 0.5) * bin_size, 3)
    return {"bin_size": bin_size, "counts": counts, "total": total,
            "poc": poc, "poc_bin": poc_i,
            "high": round((max(counts) + 1) * bin_size, 3),
            "low": round(min(counts) * bin_size, 3)}


def value_area(profile: Optional[dict], coverage: float = 0.70) -> Optional[dict]:
    """VAH/VAL enclosing ``coverage`` (default 70%) of the TPOs around the POC.

    Standard method: start at the POC bin and repeatedly add the richer of the two
    adjacent bins (above / below) until the accumulated tally reaches ``coverage``
    of the total. VAH = top of the highest VA bin, VAL = bottom of the lowest.
    """
    if not profile or not profile.get("counts"):
        return None
    counts: Dict[int, int] = profile["counts"]
    total = profile["total"]
    bs = profile["bin_size"]
    target = coverage * total

    poc_i = profile["poc_bin"]
    lo_i = hi_i = poc_i
    acc = counts[poc_i]
    lo_edge = min(counts)
    hi_edge = max(counts)
    while acc < target and (lo_i > lo_edge or hi_i < hi_edge):
        up = counts.get(hi_i + 1, 0) if hi_i < hi_edge else -1
        dn = counts.get(lo_i - 1, 0) if lo_i > lo_edge else -1
        if up < 0 and dn < 0:
            break
        if up >= dn:                      # ties expand upward
            hi_i += 1
            acc += counts.get(hi_i, 0)
        else:
            lo_i -= 1
            acc += counts.get(lo_i, 0)
    return {"poc": profile["poc"],
            "vah": round((hi_i + 1) * bs, 3),
            "val": round(lo_i * bs, 3),
            "coverage": round(acc / total, 3) if total else 0.0}


def breakout_confirmed(side: str, price: float, va: Optional[dict],
                       or_high: float, or_low: float) -> dict:
    """Does the profile confirm the session breakout is LEAVING BALANCE?

    Two conditions:
      1. price has cleared the value area in the trade direction (above VAH for a
         long, below VAL for a short) — not just the opening range;
      2. the POC is INSIDE the opening range (the OR was the balance/coil), so the
         break is an expansion away from fair value, not chop around the POC.

    Returns {ok, reason, poc, vah, val}. Fails safe (ok False) if no value area.
    """
    if not va:
        return {"ok": False, "reason": "no value area (thin profile)"}
    vah, val, poc = va["vah"], va["val"], va["poc"]
    s = side.lower()
    if s in ("long", "buy"):
        cleared = price > vah
    elif s in ("short", "sell"):
        cleared = price < val
    else:
        return {"ok": False, "reason": f"bad side {side!r}", **va}
    poc_in_or = or_low <= poc <= or_high
    ok = cleared and poc_in_or
    if not cleared:
        reason = f"inside value area — price {price} not beyond {'VAH '+str(vah) if s in ('long','buy') else 'VAL '+str(val)}"
    elif not poc_in_or:
        reason = f"POC {poc} outside the OR — range isn't the balance, no clean expansion"
    else:
        reason = f"leaving balance — cleared {'VAH' if s in ('long','buy') else 'VAL'}, POC in OR"
    return {"ok": ok, "reason": reason, "poc": poc, "vah": vah, "val": val}
