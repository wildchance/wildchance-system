"""STRATOPS muster — collect every live candidate, sort, and allocate (network glue).

Runs AFCENT's scanners, normalises each fired signal into a candidate with its
campaign framing, then hands the set to gold.stratops for scoring + allocation
under ARCENT's exposure cap. Read-only: it does not open positions — it returns the
engagement list (take / hold / stand-down) for the operator (or a follow-up
execute step) to act on.
"""

from __future__ import annotations

from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from services import gold_scan, gold_intraday
from services.candlerange_service import crt_read
from services import gold_positions as gp
from gold.limit_orders import size_limit
from gold.objective import advances, campaign_objective
from gold import stratops
from gold import zones as gz
from gold import macro_cycle as gcycle
from gold import timeline as tl
from gold import flip_ladders as fl
from propfirm.engine import evaluate_trade, DEFAULT_TIER


def _with_campaign(sig: dict) -> dict:
    if sig and "campaign" not in sig and sig.get("entry") is not None:
        side = "long" if sig.get("signal") == "LONG" else "short"
        sig["campaign"] = advances(side, sig["entry"])
    return sig


def _sniper_candidate(layer: dict, side: str, target: float, balance: float,
                      tier: str) -> dict:
    """Enrich one sniper-stack layer into a fully-scored STRATOPS candidate."""
    entry, stop = layer["entry"], layer["stop"]
    s = "long" if side == "buy" else "short"
    dist = abs(entry - stop)
    tps = ([{"price": round(target, 2), "rr": round(abs(target - entry) / dist, 2)}]
           if target and dist else [])
    return {
        "signal": "LONG" if side == "buy" else "SHORT", "trade_type": "sniper",
        "instrument": "XAU/USD", "kind": "limit",
        "entry": entry, "stop": stop, "lot": layer["lot"], "risk_usd": layer["risk_usd"],
        "targets": tps,
        "gate": evaluate_trade(balance, layer["risk_usd"], tier=tier),
        "campaign": advances(s, entry),
        "regime": gcycle.regime_gate(s),
        "htf_confluence": tl.htf_confluence(s, entry),
        "location": {"ok": True, "note": f"at OB zone {layer['zone']}"},
        "liquidity_draw": {"price": round(target, 2)} if target else None,
        "profile": layer["zone"], "justification": layer["reason"],
    }


def _zone_candidates(price: float, balance: float, risk_usd: float,
                     tier: str = DEFAULT_TIER, layers: int = 3) -> list:
    """Sniper-stack candidates for the campaign-aligned OB zone (buy the discount
    shelf below when the campaign is long, sell the premium shelf above when short),
    targeting the opposite rail (the round-trip bag)."""
    zf = gz.zone_for(price)
    direction = campaign_objective(price).get("direction")
    if direction == "long" and zf.get("nearest_below"):
        zone, target = zf["nearest_below"], (zf.get("nearest_above") or {}).get("low")
    elif direction == "short" and zf.get("nearest_above"):
        zone, target = zf["nearest_above"], (zf.get("nearest_below") or {}).get("high")
    else:
        return []
    stack = gz.sniper_stack(zone["name"], balance=balance, risk_usd=risk_usd,
                            layers=layers, target_price=target)
    if stack.get("signal") not in ("LONG", "SHORT"):
        return []
    return [_sniper_candidate(l, zone["side"], target, balance, tier)
            for l in stack["orders"]]


async def muster(db: AsyncSession, balance: float = 5000.0,
                 risk_usd: float = 20.0, deploy: bool = False) -> dict:
    """Gather candidates from the tiers + swing + CRT, score, and allocate.

    ``deploy=True`` is the P4 paper-run: every allocated ("take") candidate is
    opened as a tracked position (source stratops_paper) so the scorecard measures
    STRATOPS itself — run on a schedule until the reflection verdict is GREEN."""
    cands: List[dict] = []
    locked_out: List[dict] = []
    # STRICT 2026 DXY rule: are trend longs locked? (gold not bullish until DXY
    # flips bearish). Range-fade limits — sniper/prelondon/intrasession — are
    # mean-reversion and stay allowed; only swing/intraday TREND longs are locked.
    _long_lock = gcycle.regime_gate("long", strict=True)
    _trend_locked = (not _long_lock["ok"]) and ("strict:" in _long_lock["reason"])

    def add(sig):
        if not sig or sig.get("signal") not in ("LONG", "SHORT"):
            return
        if (sig.get("signal") == "LONG" and _trend_locked
                and (sig.get("trade_type") in ("swing", "intraday"))):
            locked_out.append({"trade_type": sig.get("trade_type"),
                               "entry": sig.get("entry"), "reason": _long_lock["reason"]})
            return
        cands.append(_with_campaign(sig))

    # MARCENT / AFCENT — the tiered intraday scan (protraction softened to a score).
    add(await gold_intraday.scan(balance=balance, risk_usd=risk_usd,
                                 require_protraction=False, notify=False))
    # The weekly-profile swing scan.
    add(await gold_scan.scan(balance=balance, risk_usd=risk_usd, notify=False))
    # SOCCENT — confirmed CRT strikes (Asian + NY).
    for sess in ("asia", "ny"):
        r = await crt_read("XAU/USD", session=sess)
        conf = (r or {}).get("confirmation") or {}
        if conf.get("confirmed"):
            add(size_limit(conf["order"], balance, risk_usd))

    # ENGINEERS — sniper limit stacks at the campaign-aligned OB zone (the layers
    # compete; the best-priced one deploys, deduped to one sniper position/side/day).
    try:
        from utils.price_fetcher import get_forex_price
        _price = await get_forex_price("XAU/USD")
        for c in _zone_candidates(_price, balance, risk_usd):
            add(c)
    except Exception:
        pass

    positions = await gp.list_positions(db, status="OPEN", limit=100)
    positions += await gp.list_positions(db, status="PENDING", limit=100)

    result = stratops.allocate(cands, positions)
    result["candidates"] = len(cands)
    result["dxy_long_lock"] = {"trend_longs_locked": _trend_locked,
                               "status": _long_lock.get("dxy_flip"),
                               "locked_out": locked_out}

    # P4 paper deploy — open each allocated candidate as a tracked position.
    if deploy and result["take"]:
        deployed = []
        for row in result["take"]:
            card = cands[row["idx"]]
            opened = await gp.open_from_signal(db, card, source="stratops_paper")
            deployed.append({"trade_type": row["trade_type"], "score": row["score"],
                             "position": opened})
        result["deployed"] = deployed
    # Surface the standing campaign objective even if no candidate fired.
    try:
        from utils.price_fetcher import get_forex_price
        price = await get_forex_price("XAU/USD")
        result["campaign"] = campaign_objective(price)
    except Exception:
        pass
    # Which flip tier the account sits in — the run cadence the sizing serves.
    tier_plan = fl.plan(balance)
    result["account"] = {"balance": balance, "flip_tier": tier_plan["tier"],
                         "pip_cadence": tier_plan.get("pip_target") or tier_plan.get("cadence"),
                         "note": tier_plan["note"]}
    return result
