"""Kingdom report (B1-B14 assembler) — compose every existing branch into one
structured 14-branch intelligence JSON, with the daily-CBDR ingestion hook.

This is the deliverable the '14-Branch Framework' prompt actually wants: it does NOT
reinvent the branches — it gathers what the system already computes (dxy, macro,
cbdr, radar/SMC, liquidity, options, cot, intermarket, volatility, trap, retracement,
stratops, news) into the report structure, plus the Kingdom Consensus table and the
Vaultum Directive (regime-invalidation checklist + temporal risk). Data-gated branches
(footprint L2/L3, order-flow delta) are marked 'unavailable' honestly rather than
faked. Every branch is best-effort — one failing never sinks the report.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession


def _bias_vote(b: Optional[str]) -> int:
    return {"long": 1, "bull": 1, "bullish": 1,
            "short": -1, "bear": -1, "bearish": -1}.get((b or "").lower(), 0)


async def kingdom_report(db: AsyncSession, asset: str = "XAU/USD",
                         price: Optional[float] = None,
                         cbdr_high: Optional[float] = None,
                         cbdr_low: Optional[float] = None,
                         interval: str = "4h") -> dict:
    branches: dict = {}
    consensus: list = []

    # ---- shared data (fetched once, guarded) --------------------------------
    if price is None:
        try:
            from utils.price_fetcher import get_forex_price
            price = await get_forex_price(asset)
        except Exception:
            price = None
    daily = htf_bias = box = None
    try:
        from services.ohlc_service import fetch_ohlc
        daily = await fetch_ohlc(asset, "1day", 120)
        if price is None and daily:
            price = daily[-1][4]
    except Exception:
        pass
    try:
        from gold import radar as grd
        if daily and len(daily) >= 8:
            wk = await fetch_ohlc(asset, "1week", 60)
            mo = await fetch_ohlc(asset, "1month", 48)
            htf_bias = grd.combine_htf(
                daily=grd.order_blocks(daily, timeframe="1D"),
                weekly=grd.order_blocks(wk, timeframe="1W") if len(wk) >= 8 else [],
                monthly=grd.order_blocks(mo, timeframe="1M") if len(mo) >= 8 else [],
            ).get("htf_bias")
    except Exception:
        pass
    # CBDR — daily ingestion protocol: explicit high/low wins, else the live box.
    try:
        from cbdr.engine import build_cbdr, read_bias, extension_read
        if cbdr_high is not None and cbdr_low is not None:
            box = build_cbdr(float(cbdr_high), float(cbdr_low))
        else:
            from services.recon_service import _live_box
            box = await _live_box("prelondon")
    except Exception:
        box = None

    def add(n, name, payload, bias=None, confidence=None):
        branches[f"B{n}"] = {"name": name, **payload}
        consensus.append({"branch": f"B{n}", "name": name, "bias": bias,
                          "confidence": confidence})

    # ---- B1 Sovereign Macro + paradox resolution ----------------------------
    try:
        from gold import macro_cycle as mc, dxy as gdxy
        reg = mc.regime_read()
        flip = gdxy.dxy_flip_status()
        paradox = None
        if not flip["unlocked"]:
            paradox = ("yield/asset paradox resolved: DXY has NOT flipped — gold longs "
                       "structurally LOCKED despite any bullish narrative (2026 dollar-up)")
        add(1, "Sovereign Macro Intelligence",
            {"regime": reg.get("regime"), "gold_bias": reg.get("gold_bias"),
             "dxy_flip": flip.get("gold_longs"), "fused_verdict": reg.get("fused_verdict"),
             "contradictions": reg.get("contradictions"),
             "macro_paradox_resolution": paradox},
            bias=reg.get("gold_bias"), confidence=reg.get("confluence_score"))
    except Exception as e:
        add(1, "Sovereign Macro Intelligence", {"error": str(e)})

    # ---- B2 CBDR Execution Engine -------------------------------------------
    try:
        from cbdr.engine import read_bias, extension_read
        if box is not None:
            rb = read_bias(float(price), box) if price else {}
            add(2, "CBDR Execution Engine",
                {"ingestion": ("operator-fed daily high/low" if cbdr_high is not None
                               else "live pre-London box"),
                 "box": {"high": box.high, "low": box.low, "mid": box.mid,
                         "range": box.range},
                 "sd_levels": box.levels, "read": rb,
                 "extension": extension_read(float(price), box) if price else None,
                 "volume_profile": {"status": "unavailable — no volume feed (POC/VAH/VAL)"}},
                bias=(rb or {}).get("bias"))
        else:
            add(2, "CBDR Execution Engine", {"status": "no CBDR box"})
    except Exception as e:
        add(2, "CBDR Execution Engine", {"error": str(e)})

    # ---- B3 Liquidity Warfare -----------------------------------------------
    try:
        from services.gold_liquidity import liquidity_map
        lm = await liquidity_map(asset)
        add(3, "Liquidity Warfare Division", lm if isinstance(lm, dict) else {"map": lm})
    except Exception as e:
        add(3, "Liquidity Warfare Division", {"error": str(e)})

    # ---- B4 Smart Money Concepts --------------------------------------------
    try:
        from gold import radar as grd
        scan = grd.radar_scan(daily, float(price)) if (daily and price) else {}
        add(4, "Smart Money Concepts",
            {"htf_orb_bias": htf_bias, "active_retest": scan.get("active_retest"),
             "continuity": scan.get("continuity"),
             "note": "MTF order-block structure (D/W/M); breaker-block not yet modelled"},
            bias=htf_bias)
    except Exception as e:
        add(4, "Smart Money Concepts", {"error": str(e)})

    # ---- B5 Footprint (data-gated) ------------------------------------------
    try:
        from gold import options_flow as of
        add(5, "Footprint Analysis",
            {"status": "L2/L3 COMEX / LBMA / SGE / delta unavailable (no institutional "
                       "order-book feed) — options flow is the affordable proxy",
             "options_proxy": of.snapshot()})
    except Exception as e:
        add(5, "Footprint Analysis", {"error": str(e)})

    # ---- B6 Delta (data-gated) ----------------------------------------------
    add(6, "Delta Analysis",
        {"status": "unavailable — cumulative/order-flow delta needs tick/L2 data"})

    # ---- B7 COT Positioning + projection ------------------------------------
    try:
        from services.cot_projection_service import project_cot
        proj = await project_cot(asset)
        add(7, "COT Positioning", proj,
            bias=("long" if (proj.get("projected_net") or 0) > 0 else "short"))
    except Exception as e:
        add(7, "COT Positioning", {"error": str(e)})

    # ---- B8 Central Bank Intelligence ---------------------------------------
    try:
        from gold import purchases_audit as gpa
        add(8, "Central Bank Intelligence",
            {"positioning": gpa.positioning_state(), "liquidity": gpa.liquidity_state()})
    except Exception as e:
        add(8, "Central Bank Intelligence", {"error": str(e)})

    # ---- B9 Volatility Engine -----------------------------------------------
    try:
        from gold import volatility as gv
        vbars = await fetch_ohlc(asset, interval, 120)
        vread = gv.volatility_read(vbars, float(price) if price else None)
        add(9, "Volatility Engine", vread,
            confidence=(vread.get("regime") or {}).get("regime"))
    except Exception as e:
        add(9, "Volatility Engine", {"error": str(e)})

    # ---- B10 Trap Detection --------------------------------------------------
    try:
        from gold import trap_probability as gt
        tbars = await fetch_ohlc(asset, interval, 10)
        level = None
        if box is not None and price:
            level = box.levels.get("+1SD") if price >= box.mid else box.levels.get("-1SD")
        tp = gt.trap_probabilities(tbars, level) if level else {"status": "no level"}
        add(10, "Trap Detection Network", tp, bias=tp.get("implied_bias"))
    except Exception as e:
        add(10, "Trap Detection Network", {"error": str(e)})

    # ---- B11 Intermarket Intelligence ---------------------------------------
    try:
        from services.intermarket_service import intermarket_matrix
        im = await intermarket_matrix(asset)
        add(11, "Intermarket Intelligence", im,
            confidence=im.get("net_score"))
    except Exception as e:
        add(11, "Intermarket Intelligence", {"error": str(e)})

    # ---- B12 Regime-invalidation checklist (kernel; IDM theatre dropped) -----
    try:
        from gold import regime_checklist as rc, macro_cycle as mc, dxy as gdxy
        from gold import purchases_audit as gpa
        reg = mc.regime_read()
        inval = None
        if box is not None:
            inval = box.levels.get("-1.5SD") if reg.get("gold_bias") == "long" \
                else box.levels.get("+1.5SD")
        cl = rc.build_checklist(
            regime_bias=reg.get("gold_bias", "neutral"),
            dxy_unlocked=gdxy.dxy_flip_status().get("unlocked"),
            real_rate_direction=(mc.INPUTS.get("real_rate_direction")),
            cot_zone=gpa.positioning_state().get("zone"),
            liquidity_state=gpa.liquidity_state().get("state"),
            htf_bias=htf_bias, price=float(price) if price else None,
            invalidation_level=inval)
        add(12, "Regime Invalidation Checklist", cl, confidence=cl.get("verdict"))
    except Exception as e:
        add(12, "Regime Invalidation Checklist", {"error": str(e)})

    # ---- B13 Kingdom Strategic Command --------------------------------------
    try:
        from services.stratops_service import muster
        m = await muster(db)
        add(13, "Kingdom Strategic Command",
            {"take": m.get("take"), "hold": m.get("hold"), "stand_down": m.get("stand_down"),
             "campaign": m.get("campaign"), "dxy_long_lock": m.get("dxy_long_lock"),
             "retracement": m.get("retracement"), "account": m.get("account")})
    except Exception as e:
        add(13, "Kingdom Strategic Command", {"error": str(e)})

    # ---- B14 Temporal Intelligence & Event Horizon --------------------------
    try:
        from gold import event_horizon as eh
        from services.news_guard import high_impact_calendar
        cal = await high_impact_calendar()
        now = _dt.datetime.now(_dt.timezone.utc)
        events = []
        for ev in cal or []:
            try:
                d = _dt.datetime.fromisoformat(ev["date"]).replace(tzinfo=_dt.timezone.utc)
                events.append({"name": ev.get("event") or "", "ccy": ev.get("ccy"),
                               "hours_until": (d - now).total_seconds() / 3600.0})
            except Exception:
                continue
        add(14, "Temporal Intelligence & Event Horizon", eh.event_horizon(events))
    except Exception as e:
        add(14, "Temporal Intelligence & Event Horizon", {"error": str(e)})

    # ---- Kingdom Consensus + Vaultum Directive ------------------------------
    votes = [_bias_vote(c["bias"]) for c in consensus if c.get("bias")]
    score = sum(votes)
    net_bias = "long" if score > 0 else "short" if score < 0 else "neutral"
    directive = branches.get("B12", {})
    temporal = (branches.get("B14", {}) or {}).get("stack", {})
    return {
        "asset": asset, "price": round(float(price), 2) if price else None,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "cbdr_ingested": cbdr_high is not None and cbdr_low is not None,
        "branches": branches,
        "kingdom_consensus": {
            "table": consensus, "net_bias": net_bias, "vote_score": score,
            "bullish": sum(1 for v in votes if v > 0),
            "bearish": sum(1 for v in votes if v < 0)},
        "vaultum_directive": {
            "regime_invalidation": {"verdict": directive.get("verdict"),
                                    "failed": directive.get("failed"),
                                    "note": directive.get("note")},
            "temporal_risk": temporal,
            "net_bias": net_bias,
            "note": (f"Kingdom net bias {net_bias.upper()} (score {score:+d}); "
                     f"regime {directive.get('verdict', 'n/a')}; "
                     f"temporal vol ×{temporal.get('vol_multiplier', 1.0)}")},
        "data_gated": ["B5 footprint (L2/L3)", "B6 order-flow delta"],
    }
