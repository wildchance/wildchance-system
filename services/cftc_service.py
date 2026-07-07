"""CFTC Commitments of Traders (COT) for COMEX gold — guarded, additive.

Pulls the latest weekly legacy COT for gold from the CFTC public reporting API
(Socrata — free, no key). Feeds the regime filter's tactical positioning read:
non-commercial net and open interest, plus a WoW change. Returns None on any
failure so the regime stack falls back to the encoded snapshot.

CFTC releases Fridays 3:30pm ET for the prior Tuesday.
"""

from __future__ import annotations

from typing import Optional

import httpx

# Socrata legacy futures-only COT dataset.
CFTC_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
# COMEX gold legacy market code.
GOLD_MARKET_CODE = "088691"


def net_and_oi(row: dict) -> Optional[dict]:
    """Extract non-commercial net + open interest + report date from a COT row."""
    try:
        long_ = float(row["noncomm_positions_long_all"])
        short = float(row["noncomm_positions_short_all"])
        oi = float(row["open_interest_all"])
    except (KeyError, ValueError, TypeError):
        return None
    return {"noncomm_net": int(long_ - short), "open_interest": int(oi),
            "report_date": row.get("report_date_as_yyyy_mm_dd", "")[:10]}


async def gold_cot() -> Optional[dict]:
    """Latest two weekly gold COT prints → net, OI, and the WoW net change."""
    params = {"cftc_contract_market_code": GOLD_MARKET_CODE,
              "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 2}
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            rows = (await c.get(CFTC_URL, params=params)).json()
    except Exception:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    latest = net_and_oi(rows[0])
    if latest is None:
        return None
    prev = net_and_oi(rows[1]) if len(rows) > 1 else None
    latest["wow_net_change"] = (latest["noncomm_net"] - prev["noncomm_net"]) if prev else None
    return latest
