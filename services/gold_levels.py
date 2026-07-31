"""Auto level refresh — detect today's structure and load it into Optimus.

Runs level_finder on fetched OHLC and pushes the result into the live Optimus map
(set_live_zones + set_sell_path), so the reject-gate + alerter always sit on the
current OB/swing/target structure — no more hand-feeding levels. Best-effort; on a
data miss it leaves the existing map untouched.
"""

from __future__ import annotations

from typing import Optional


async def refresh(interval: str = "4h", bars: int = 200, apply: bool = True) -> dict:
    """Detect levels from XAU/USD OHLC and (optionally) load them into Optimus."""
    from services.ohlc_service import fetch_ohlc
    from gold import level_finder as lf
    from gold import optimus as gop

    ohlc = await fetch_ohlc("XAU/USD", interval, bars)
    if not ohlc or len(ohlc) < 20:
        return {"ok": False, "reason": "not enough XAU/USD bars", "have": len(ohlc or [])}

    levels = lf.build_levels(ohlc)
    if not levels.get("ok"):
        return {"ok": False, "reason": levels.get("reason", "no levels")}

    applied = None
    if apply:
        # push zones + the sell staircase into the live Optimus map
        if levels["sell_zones"] or levels["buy_zones"]:
            gop.set_live_zones(sell=levels["sell_zones"] or None,
                               buy=levels["buy_zones"] or None,
                               pivots={"recent_high": levels["pivots"]["recent_high"],
                                       "recent_low": levels["pivots"]["recent_low"],
                                       "auto": True})
        if levels["sell_retest_levels"]:
            gop.SELL_RETEST_LEVELS = sorted(set(levels["sell_retest_levels"]
                                                + levels["floors"]), reverse=True)
        if levels["sell_retest_levels"] and levels["floors"]:
            tp = min(levels["floors"])
            gop.set_sell_path(sells=levels["sell_retest_levels"],
                              floors=levels["floors"], tp=tp)
            applied = {"sells": levels["sell_retest_levels"], "floors": levels["floors"], "tp": tp}

    return {
        "ok": True, "interval": interval, "bars": len(ohlc),
        "price": levels["price"], "atr": levels["atr"],
        "sell_retest_levels": levels["sell_retest_levels"], "floors": levels["floors"],
        "sell_zones": levels["sell_zones"], "buy_zones": levels["buy_zones"],
        "pivots": levels["pivots"], "counts": levels["counts"],
        "applied_to_optimus": bool(applied), "applied": applied,
        "note": ("levels auto-detected + loaded into Optimus" if applied
                 else "levels detected (preview only — apply=false)"),
    }
