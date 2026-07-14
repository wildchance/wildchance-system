"""Cross-session CBDR confluence — Asian premium sold into London discount.

The observed gold edge: price sweeps the **Asian CBDR +1/+1.5 SD premium**, then
runs down 500–1500 pips into the **London CBDR discount**; and the **pre-London
−1/−1.5 SD discount** gets bought back to premium. This engine chains the two
session boxes into confluence-scored LIMIT orders — pre-calculated levels you arm
and wait for, not breakouts you chase.

The score is the quality gate. An SD-zone limit only earns conviction when the
higher-timeframe read AGREES with the fade: sell Asian premium only when the
weekly/macro bias is down or neutral (selling +1.5 in a raging uptrend is how you
get run over). This is what turns a two-week observation into a filtered system —
and every fill is validated by the backtest before any live account.

  conviction(score)                          A/B/C label
  cross_session_confluence(asian, london…)   scored buy/sell limits chaining boxes
"""

from __future__ import annotations

from typing import Optional

from cbdr.engine import CBDR, GREY_ZONE_SD


def _bias_num(b) -> int:
    """long/bullish → +1, short/bearish → −1, neutral/unknown → 0."""
    s = (b or "neutral").lower() if isinstance(b, str) else "neutral"
    if s in ("long", "buy", "bull", "bullish"):
        return 1
    if s in ("short", "sell", "bear", "bearish"):
        return -1
    return 0


def conviction(score: float) -> str:
    """Conviction tier from a 0–100 confluence score."""
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "-"


def _score(direction: int, weekly: int, macro: int, geometry_ok: bool) -> int:
    """Confluence score for a fade in ``direction`` (+1 buy / −1 sell).

    40 base for the SD zone; ±30 weekly-bias agreement; ±20 macro; +10 geometry.
    A directly-opposing weekly bias is the big penalty — that's the filter that
    stops us fading into a strong trend.
    """
    s = 40
    s += 30 if weekly == direction else (10 if weekly == 0 else -30)
    s += 20 if macro == direction else (5 if macro == 0 else -15)
    s += 10 if geometry_ok else 0
    return max(0, min(100, s))


def cross_session_confluence(asian: CBDR, london: Optional[CBDR] = None,
                             weekly_bias: str = "neutral", macro_bias: str = "neutral",
                             grey=GREY_ZONE_SD, min_score: int = 50,
                             buffer: float = 1.0) -> dict:
    """Scored LIMIT orders chaining the Asian and London/pre-London CBDR boxes.

    SELL: arm at the Asian +1SD premium, stop beyond +2SD, targets ladder DOWN to
    the London discount if a ``london`` box is given, else the Asian downside SD
    (−1/−2/−3SD — the 500/1000/1500-pip projections).
    BUY:  arm at the London (or Asian, if no london) −1SD discount, stop beyond
    −2SD, targets ladder UP to premium.

    Only orders scoring ≥ ``min_score`` survive; they're returned high-score-first.
    Each order is a ready limit spec {side, entry, stop, targets, score, conv, …}.
    """
    wk, mc = _bias_num(weekly_bias), _bias_num(macro_bias)
    lo_sd, hi_sd = grey
    a = asian.levels
    tgt_box = london if london is not None else asian
    tl = tgt_box.levels
    orders = []

    # --- SELL the Asian premium → London (or Asian) discount --------------------
    a_prem = a.get(f"+{lo_sd:g}SD")
    a_prem2 = a.get("+2SD")
    disc1 = tl.get(f"-{lo_sd:g}SD")
    disc2 = tl.get("-2SD") or disc1
    disc3 = tl.get("-3SD") or disc2
    if a_prem and a_prem2 and disc1:
        geometry_ok = a_prem > disc1                 # premium sits above the discount
        sc = _score(-1, wk, mc, geometry_ok)
        targets = [round(tgt_box.mid, 2), round(disc1, 2), round(disc2, 2), round(disc3, 2)]
        orders.append({
            "side": "short", "kind": "limit", "trade_type": "cbdr_confluence",
            "level": f"asian+{lo_sd:g}SD", "entry": round(a_prem, 2),
            "stop": round(a_prem2 + buffer, 2), "targets": targets,
            "score": sc, "conviction": conviction(sc), "geometry_ok": geometry_ok,
            "grey_zone": [round(a_prem, 2), round(a.get(f"+{hi_sd:g}SD") or a_prem, 2)],
            "reason": (f"sell Asian +{lo_sd:g}SD premium → "
                       f"{'London' if london is not None else 'Asian'} discount "
                       f"(weekly {weekly_bias}, macro {macro_bias})"),
        })

    # --- BUY the discount → premium --------------------------------------------
    b_disc = tl.get(f"-{lo_sd:g}SD")
    b_disc2 = tl.get("-2SD")
    prem1 = tl.get(f"+{lo_sd:g}SD")
    prem2 = tl.get("+2SD") or prem1
    if b_disc and b_disc2 and prem1:
        sc = _score(+1, wk, mc, True)
        targets = [round(tgt_box.mid, 2), round(prem1, 2), round(prem2, 2)]
        orders.append({
            "side": "long", "kind": "limit", "trade_type": "cbdr_confluence",
            "level": f"{'london' if london is not None else 'asian'}-{lo_sd:g}SD",
            "entry": round(b_disc, 2), "stop": round(b_disc2 - buffer, 2),
            "targets": targets, "score": sc, "conviction": conviction(sc),
            "geometry_ok": True,
            "grey_zone": [round(b_disc, 2), round(tl.get(f"-{hi_sd:g}SD") or b_disc, 2)],
            "reason": (f"buy {'London/pre-London' if london is not None else 'Asian'} "
                       f"−{lo_sd:g}SD discount → premium "
                       f"(weekly {weekly_bias}, macro {macro_bias})"),
        })

    orders = [o for o in orders if o["score"] >= min_score]
    orders.sort(key=lambda o: -o["score"])
    return {
        "orders": orders,
        "asian_box": {"high": asian.high, "low": asian.low, "mid": round(asian.mid, 2),
                      "range": round(asian.range, 2)},
        "london_box": ({"high": london.high, "low": london.low,
                        "mid": round(london.mid, 2), "range": round(london.range, 2)}
                       if london is not None else None),
        "weekly_bias": weekly_bias, "macro_bias": macro_bias, "min_score": min_score,
    }
