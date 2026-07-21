"""Gold purchases & positioning audit — YoY / QoQ / MoM / WoW (pure, stdlib-only).

Encodes the change audit compiled from WGC (central-bank tonnage, ETF holdings/
flows) and CFTC COT (non-commercial net, open interest), as of 2026-07-06, and
reproduces the read-outs so the system can state *what changed and when* rather
than re-eyeballing a chart.

The durable signal here (feeds the regime filter in gold.macro_cycle):
  • central-bank demand is PRICE-SENSITIVE, not an infinite bid — April 2026
    collapsed to 17t (a record low) exactly as price cracked $4,200;
  • ETF "growth" is a MARK-TO-MARKET illusion — holdings +3.8% YoY while AUM
    +132%, i.e. almost no new physical accumulation;
  • speculators are WASHED OUT, not stretched — COT non-comm 181k vs the 251k
    January peak, with room to add;
  • LIQUIDITY is drained — open interest −33% YoY, so moves get more violent.
"""

from __future__ import annotations

from typing import List, Optional

# --- raw series (tonnes unless noted) --------------------------------------
CB_YOY = [("2022", 1135), ("2023", 1037), ("2024", 1045), ("2025", 863), ("2026e", 850)]
CB_QOQ = [("Q1'25", 290), ("Q2'25", 125), ("Q3'25", 220), ("Q4'25", 228),
          ("Q1'26", 290), ("Q2'26", 180)]
CB_MOM_2026 = [("Jan", 32), ("Feb", 28), ("Mar", 35), ("Apr", 17), ("May", 22), ("Jun", 18)]

# COT COMEX non-commercial net (contracts), weekly waypoints.
COT_WOW = [("6 Jan", 227632), ("13 Jan", 251238), ("27 Jan", 205396),
           ("26 May", 154260), ("2 Jun", 176020), ("23 Jun", 181339)]

SNAPSHOT = {
    "as_of": "2026-07-21",
    "cb_purchases_2026e_t": 850,          # −1.5% YoY, still elevated vs pre-2022 ~500t
    "etf_holdings_t": 4050,               # −126t off the Feb 4,176t record; +3.0% YoY
    "etf_aum_usd_bn": 590,                # price-driven, not flows
    "etf_h1_flows_usd_bn": 14,            # vs +38bn H1'25 (−63%)
    "cot_noncomm_net": 170000,            # 2026-07-21 est, rebuilding from 154,260 trough
    "cot_peak": 251238, "cot_trough": 154260,
    "cot_open_interest": 340000,          # −35% from the 528k peak — liquidity drained
    "cot_oi_peak": 528000,
    "commercial_net": -205404,
    "gold_price": 4100.0,                 # ~WGC fair value; discount to structural case
    "etf_at_loss_t": 200,                 # overhead supply on rallies
}


def feed(as_of: str = None, **kwargs) -> dict:
    """Operator-feed the WGC/COT snapshot from an audit report (only known keys)."""
    for k, v in kwargs.items():
        if v is not None and k in SNAPSHOT:
            SNAPSHOT[k] = v
    SNAPSHOT["as_of"] = as_of or "operator-fed"
    return dict(SNAPSHOT)


def liquidity_state() -> dict:
    """Open-interest depth read — a thin tape means wider stops + sharper sweeps."""
    oi = SNAPSHOT["cot_open_interest"]
    peak = SNAPSHOT.get("cot_oi_peak", 528000)
    vs_peak = round((oi - peak) / peak * 100, 1) if peak else None
    impaired = oi < 0.80 * peak            # >20% below the OI peak = drained
    return {"open_interest": oi, "peak": peak, "vs_peak_pct": vs_peak,
            "state": "impaired" if impaired else "normal",
            "stop_widen_mult": 1.25 if impaired else 1.0,
            "note": ("thin tape — widen stops ~25%, expect sharper sweeps / slippage"
                     if impaired else "normal market depth")}


def _pct(a: float, b: float) -> Optional[float]:
    return round((b - a) / a * 100, 1) if a else None


def _changes(series: List[tuple]) -> List[dict]:
    out = []
    for i, (label, val) in enumerate(series):
        prev = series[i - 1][1] if i > 0 else None
        out.append({"period": label, "value": val,
                    "change_pct": _pct(prev, val) if prev is not None else None})
    return out


def audit() -> dict:
    """Full YoY/QoQ/MoM/WoW change audit + the cross-metric findings."""
    return {
        "as_of": SNAPSHOT["as_of"],
        "central_bank": {
            "yoy": _changes(CB_YOY),
            "qoq": _changes(CB_QOQ),
            "mom_2026": _changes(CB_MOM_2026),
            "read": "price-sensitive bid — Apr 17t (record low) as price cracked $4,200; "
                    "Q2'26 −37.9% QoQ; still ~850t/yr, elevated vs pre-2022 ~500t",
        },
        "etf": {
            "holdings_t": SNAPSHOT["etf_holdings_t"],
            "yoy_holdings_pct": 3.8, "yoy_aum_pct": 132.0,
            "h1_flows_usd_bn": SNAPSHOT["etf_h1_flows_usd_bn"],
            "read": "mark-to-market illusion — holdings +3.8% YoY vs AUM +132%; "
                    f"{SNAPSHOT['etf_at_loss_t']}t+ at a loss = overhead supply on rallies",
        },
        "cot": {
            "wow": _changes(COT_WOW),
            "net": SNAPSHOT["cot_noncomm_net"],
            "peak": SNAPSHOT["cot_peak"], "trough": SNAPSHOT["cot_trough"],
            "open_interest": SNAPSHOT["cot_open_interest"], "oi_yoy_pct": -33.2,
            "read": "washed out, not stretched — 181k vs 251k peak, room to add; "
                    "OI −33% YoY = liquidity drained, moves more violent both ways",
        },
        "price": {"level": SNAPSHOT["gold_price"], "yoy_pct": 24.8, "from_ath_pct": -25.5},
        "findings": [
            "central-bank buying is price-sensitive — not an infinite bid",
            "ETF AUM growth is mark-to-market, not new physical demand",
            "specs have room to add (181k vs 251k) — positioning not stretched",
            "open interest collapse (−33%) = thin tape, violent moves",
            "200t+ of ETF gold at a loss = supply overhang into rallies",
        ],
    }


def positioning_state() -> dict:
    """COT read the regime filter consumes: is spec positioning a headwind or not?

    <150k moderate (entry ok) · 150–200k neutral · >200k stretched (contrarian
    bearish / distribution risk) · <0 net short (contrarian bullish).
    """
    net = SNAPSHOT["cot_noncomm_net"]
    if net < 0:
        zone, note = "net_short", "extreme pessimism — contrarian bullish"
    elif net < 150000:
        zone, note = "moderate", "room to add — trend entries ok"
    elif net <= 200000:
        zone, note = "neutral", "mid-range — no positioning objection"
    else:
        zone, note = "stretched", "crowded long — contrarian bearish / distribution risk"
    return {"net": net, "zone": zone, "note": note,
            "off_trough": net - SNAPSHOT["cot_trough"],
            "below_peak": SNAPSHOT["cot_peak"] - net}
