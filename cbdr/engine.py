"""CBDR — Central Bank Dealers Range (ICT) confluence engine.

Pure, deterministic, stdlib-only so it unit-tests without a data feed.

Definition (per the ICT methodology):
  • The CBDR box = the highest high and lowest low inside the 2:00 PM–8:00 PM
    New York time window (DST-aware; the data layer supplies the right bars).
  • One "standard deviation" = the box range (high − low).
  • Projection levels are measured OUTWARD from the box edges by whole multiples
    of the range:  +nSD = high + n·range ,  −nSD = low − n·range.
  • Read for the day's high/low: when price trades in the upper half of the box
    the lower deviations tend to act as the floor (low of day); in the lower
    half the upper deviations tend to cap it (high of day).

Conventions here are explicit constants so they are easy to adjust if your
flavour of CBDR differs.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Dict, List, Sequence, Tuple

# CBDR window in NEW YORK local time (the data layer must convert).
CBDR_START_HOUR = 14   # 2:00 PM
CBDR_END_HOUR = 20     # 8:00 PM (exclusive — hourly bars 14,15,16,17,18,19)
# Standard deviations projected outward from every CBDR box (intraday, session,
# and yearly). 2.5 and 4 are the EXTREME extensions used to catch breakouts /
# exhaustion reversals — a tag of +/-2.5 or +/-4 is the trigger to consult the
# look-back of past boxes for a likely peak/bottom before the next range forms.
DEFAULT_DEVIATIONS: Tuple[float, ...] = (1, 1.5, 2, 2.5, 3, 4)
EXTREME_SD = 2.5   # at/above this deviation a move is in "extreme" territory
# The 1–1.5 SD band on either side is the "grey zone" — where a pre-London reversal
# is watched for before the London manipulation begins.
GREY_ZONE_SD: Tuple[float, float] = (1.0, 1.5)


# ---------------------------------------------------------------------------
# Named session windows
#
# A "window" is a time-of-day box in some timezone. Bounds are minutes-from-
# midnight, half-open [start, end). A window that wraps past midnight (start >=
# end) is supported — its bars span two calendar dates but belong to ONE logical
# session, keyed by the date the window opened.
#
# SD projections are symmetric by construction (build_cbdr emits +nSD and -nSD
# with the same multiples), so a single box arms both the buy floor and the sell
# ceiling — no separate config per direction.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    key: str
    tz: str            # IANA tz the data layer requests bars in
    start_min: int     # minutes from midnight, inclusive
    end_min: int       # minutes from midnight, exclusive
    interval: str      # bar size to request, e.g. "1h" / "15min"
    label: str


def _hm(h: int, m: int = 0) -> int:
    return h * 60 + m


WINDOWS: Dict[str, Window] = {
    # Classic ICT CBDR — 2:00pm–8:00pm New York, hourly bars.
    "cbdr": Window("cbdr", "America/New_York", _hm(14), _hm(20), "1h",
                   "2:00pm-8:00pm NY (CBDR)"),
    # Pre-London accumulation box — defined in NEW YORK time so it stays aligned
    # through DST: 19:00 NY (prior evening) → 02:45 AM NY, i.e. it CLOSES at 02:45
    # ET = 06:45 UTC (EDT), right before London. Trade its ±SD limits at 02:45-03:00
    # ET. (The old UTC-anchored 18:00–02:45 box closed ~4h too early — 10:45 PM ET.)
    "prelondon": Window("prelondon", "America/New_York", _hm(19), _hm(2, 45), "15min",
                        "19:00-02:45 NY (pre-London accumulation, closes 02:45 ET)"),

    # Six recurring intraday session boxes. Defined in NEW YORK local time so the
    # data layer (timezone=America/New_York) shifts them with DST automatically —
    # that is exactly why the user sees each as a winter/summer UTC pair. The NY
    # hour is fixed; the UTC clock moves an hour twice a year, handled for free.
    #   trade1  13:00/14:00 -> 20:00/21:00 UTC  = NY 09:00-16:00
    #   trade2  17:00/18:00 -> 02:00/03:00 UTC  = NY 13:00-22:00
    #   trade3  20:00/21:00 -> 04:00/05:00 UTC  = NY 16:00-00:00
    #   trade4  01:00/02:00 -> 09:00/10:00 UTC  = NY 21:00-05:00
    #   trade5  05:00/06:00 -> 13:00/14:00 UTC  = NY 01:00-09:00
    #   trade6  08:00/09:00 -> 16:00/17:00 UTC  = NY 04:00-12:00
    "trade1": Window("trade1", "America/New_York", _hm(9),  _hm(16), "1h",
                     "NY 09:00-16:00 (13/14-20/21 UTC)"),
    "trade2": Window("trade2", "America/New_York", _hm(13), _hm(22), "1h",
                     "NY 13:00-22:00 (17/18-02/03 UTC)"),
    "trade3": Window("trade3", "America/New_York", _hm(16), _hm(0),  "1h",
                     "NY 16:00-00:00 (20/21-04/05 UTC)"),
    "trade4": Window("trade4", "America/New_York", _hm(21), _hm(5),  "1h",
                     "NY 21:00-05:00 (01/02-09/10 UTC)"),
    "trade5": Window("trade5", "America/New_York", _hm(1),  _hm(9),  "1h",
                     "NY 01:00-09:00 (05/06-13/14 UTC)"),
    "trade6": Window("trade6", "America/New_York", _hm(4),  _hm(12), "1h",
                     "NY 04:00-12:00 (08/09-16/17 UTC)"),
}


def in_window(minute: int, start_min: int, end_min: int) -> bool:
    """Is a minute-of-day inside [start, end), honouring a midnight wrap?"""
    if start_min < end_min:
        return start_min <= minute < end_min
    return minute >= start_min or minute < end_min       # wraps past midnight


def session_key(day_iso: str, minute: int, start_min: int, end_min: int) -> str:
    """Logical session date for a bar — the date the window OPENED.

    For a non-wrapping window that's just the bar's own date. For a wrapping one,
    bars after midnight (minute < end) belong to the session that opened the
    previous calendar day.
    """
    if start_min < end_min or minute >= start_min:
        return day_iso
    return (date.fromisoformat(day_iso) - timedelta(days=1)).isoformat()


def cbdr_box(highs: Sequence[float], lows: Sequence[float]) -> Tuple[float, float]:
    """Box high/low from the window's bar highs and lows."""
    if not highs or not lows:
        raise ValueError("need at least one bar to form a CBDR box")
    return max(highs), min(lows)


@dataclass
class CBDR:
    high: float
    low: float
    mid: float
    range: float
    levels: Dict[str, float]   # "+1SD" / "-1SD" ... -> price

    def to_dict(self) -> dict:
        return asdict(self)


def build_cbdr(high: float, low: float,
               deviations: Sequence[int] = DEFAULT_DEVIATIONS) -> CBDR:
    """Box + standard-deviation projection levels."""
    if high < low:
        raise ValueError("high must be >= low")
    rng = high - low
    mid = (high + low) / 2.0
    levels: Dict[str, float] = {}
    for n in deviations:
        levels[f"+{n}SD"] = high + n * rng
        levels[f"-{n}SD"] = low - n * rng
    return CBDR(high=high, low=low, mid=mid, range=rng, levels=levels)


def prelondon_limits(box: CBDR) -> dict:
    """Pre-London limit-order plan off a CBDR box (02:45-03:00 ET window).

    Buy limit at −1SD (discount); sell limits at +1SD and the +3SD extreme; and
    the 1–1.5SD grey zone on each side flagged for pre-London reversals. The grey
    zone is the "notice reversals before London" band.
    """
    lv = box.levels
    mid = round(box.mid, 2)
    orders = []
    if {"-1SD", "-2SD", "+1SD"} <= set(lv):
        orders.append({"side": "long", "kind": "limit", "level": "-1SD",
                       "entry": round(lv["-1SD"], 2), "stop": round(lv["-2SD"], 2),
                       "targets": [mid, round(lv["+1SD"], 2)], "trade_type": "prelondon",
                       "reason": "buy limit at −1SD (discount / low of range)"})
    if {"+1SD", "+2SD", "-1SD"} <= set(lv):
        orders.append({"side": "short", "kind": "limit", "level": "+1SD",
                       "entry": round(lv["+1SD"], 2), "stop": round(lv["+2SD"], 2),
                       "targets": [mid, round(lv["-1SD"], 2)], "trade_type": "prelondon",
                       "reason": "sell limit at +1SD"})
    if {"+3SD", "+4SD", "+1SD"} <= set(lv):
        orders.append({"side": "short", "kind": "limit", "level": "+3SD",
                       "entry": round(lv["+3SD"], 2), "stop": round(lv["+4SD"], 2),
                       "targets": [round(lv["+1SD"], 2), mid], "trade_type": "prelondon",
                       "reason": "sell limit at +3SD (extreme reversal)"})
    lo_sd, hi_sd = GREY_ZONE_SD
    grey = {
        "buy_grey": [lv.get(f"-{lo_sd:g}SD"), lv.get(f"-{hi_sd:g}SD")],
        "sell_grey": [lv.get(f"+{lo_sd:g}SD"), lv.get(f"+{hi_sd:g}SD")],
        "sd_band": [lo_sd, hi_sd],
        "note": f"{lo_sd:g}–{hi_sd:g}SD grey zone — watch reversals before London",
    }
    return {"orders": orders, "grey_zone": grey,
            "box": {"high": box.high, "low": box.low, "mid": box.mid, "range": box.range}}


def read_bias(price: float, box: CBDR) -> dict:
    """Where price sits relative to the box, and what it implies for the day.

    Returns the directional read plus the most likely day-extreme level.
    """
    if price > box.high:
        state = "breakout_up"
        note = "above the range — upper SDs are upside targets"
        key_level = box.levels.get("+1SD")
    elif price < box.low:
        state = "breakout_down"
        note = "below the range — lower SDs are downside targets"
        key_level = box.levels.get("-1SD")
    elif price >= box.mid:
        state = "bullish_half"
        note = "upper half — lower SDs likely act as the floor (low of day)"
        key_level = box.levels.get("-1SD")
    else:
        state = "bearish_half"
        note = "lower half — upper SDs likely cap it (high of day)"
        key_level = box.levels.get("+1SD")
    return {"price": price, "state": state, "note": note, "key_level": key_level}


def nearest_levels(price: float, box: CBDR, n: int = 2) -> List[dict]:
    """The n projection/box levels closest to a price (for 'price near a level'
    confluence)."""
    pts = {"high": box.high, "low": box.low, "mid": box.mid, **box.levels}
    ranked = sorted(pts.items(), key=lambda kv: abs(kv[1] - price))
    return [{"level": k, "price": v, "distance": abs(v - price)} for k, v in ranked[:n]]


def _sd_of_key(key: str) -> float:
    """'+2.5SD' -> 2.5 ; '-1SD' -> 1.0"""
    return float(key[1:-2])


def extension_read(price: float, box: CBDR) -> dict:
    """How far price has extended beyond the box, in SD multiples.

    Reports the furthest deviation reached up or down and whether it is in the
    EXTREME zone (>= EXTREME_SD). An extreme tag is the cue to consult the
    look-back of prior boxes for a likely peak/bottom before the next range.
    """
    if price > box.high:
        side = "up"
        reached = 0.0
        for key, lvl in box.levels.items():
            if key.startswith("+") and price >= lvl:
                reached = max(reached, _sd_of_key(key))
    elif price < box.low:
        side = "down"
        reached = 0.0
        for key, lvl in box.levels.items():
            if key.startswith("-") and price <= lvl:
                reached = max(reached, _sd_of_key(key))
    else:
        return {"side": "inside", "reached_sd": 0.0, "extreme": False,
                "note": "inside the box — no extension yet"}

    extreme = reached >= EXTREME_SD
    note = (f"{'breakout' if not extreme else 'EXTREME'} {side}: reached "
            f"{reached:g}SD" + (" — check look-back for exhaustion" if extreme else ""))
    return {"side": side, "reached_sd": reached, "extreme": extreme, "note": note}


def control_vs_box(price: float, box: CBDR) -> str:
    """Who controls a box right now: 'buyers' above it, 'sellers' below it,
    'balanced' while price is still inside the range."""
    if price >= box.high:
        return "buyers"
    if price <= box.low:
        return "sellers"
    return "balanced"


def lookback_control(boxes: Dict[str, CBDR], price: float) -> dict:
    """Map of who controls each historical box at the current price, plus the
    control streak running back from the most recent box.

    `boxes` is {label: CBDR} (e.g. {"2021": box, "2022": box, ...}); labels sort
    chronologically. Lets you see, in real time, which years buyers vs sellers
    still hold.
    """
    rows = []
    for label in sorted(boxes):
        b = boxes[label]
        rows.append({"label": label, "control": control_vs_box(price, b),
                     "high": b.high, "low": b.low})

    streak = None
    for r in reversed(rows):                       # newest first
        if streak is None:
            streak = {"control": r["control"], "since": r["label"], "years": 1}
        elif r["control"] == streak["control"]:
            streak["since"] = r["label"]
            streak["years"] += 1
        else:
            break
    return {"rows": rows, "streak": streak}
