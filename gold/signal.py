"""Gold signal assembler — turn an ICT weekly profile into a full trade card (pure).

Direction + justification come from the weekly profile; the intraday trade is a
fixed-pip SL (your 150–250-pip style), money-sized to a $ risk, with RR take-
profits and a break-even trigger, gated by the prop-firm rule. No network here —
the service layer feeds it the profile, live entry, and balance.
"""

from __future__ import annotations

from typing import Optional, Sequence

from gold.risk_engine import (
    GOLD_PIP, GOLD_USD_PER_POINT, size_for_risk, targets, breakeven_price,
)
from propfirm.engine import evaluate_trade, max_lot, DEFAULT_TIER


def assemble_structured(profile: dict, entry: float, stop: float, balance: float,
                        tier: str = DEFAULT_TIER, risk_usd: float = 20.0,
                        rr: Sequence[float] = (2, 3, 4)) -> dict:
    """Size a trade from a REAL entry+stop (Wade OTE/OB structure), money-first."""
    bias = (profile or {}).get("bias")
    if bias not in ("long", "short"):
        return {"signal": "NO TRADE", "instrument": "XAU/USD",
                "reason": (profile or {}).get("reason", "no directional profile")}
    dist = abs(entry - stop)
    if dist <= 0:
        return {"signal": "NO TRADE", "instrument": "XAU/USD",
                "reason": "invalid structure stop distance"}
    lot = max(0.01, min(size_for_risk(entry, stop, risk_usd), max_lot(balance)))
    risk_actual = round(lot * dist * GOLD_USD_PER_POINT, 2)
    tps = targets(entry, stop, bias, rr)
    for t in tps:
        t["usd"] = round(lot * abs(t["price"] - entry) * GOLD_USD_PER_POINT, 2)
    return {
        "signal": bias.upper(), "instrument": "XAU/USD",
        "profile": profile.get("profile"), "profile_id": profile.get("profile_id"),
        "justification": profile.get("reason"), "zone": profile.get("zone"),
        "entry": round(entry, 2), "stop": round(stop, 2),
        "stop_distance": round(dist, 2), "entry_mode": "structure",
        "lot": lot, "risk_usd": risk_actual,
        "targets": tps,
        "breakeven": breakeven_price(entry, tps[0]["price"]) if tps else None,
        "gate": evaluate_trade(balance, risk_actual, tier=tier),
        "balance": balance, "tier": tier,
    }


def assemble(profile: dict, entry: float, balance: float,
             tier: str = DEFAULT_TIER, risk_usd: float = 20.0,
             sl_pips: float = 200.0, rr: Sequence[float] = (2, 3, 4)) -> dict:
    """Build the gold trade card from a classified weekly profile + live entry."""
    bias = (profile or {}).get("bias")
    if bias not in ("long", "short"):
        return {"signal": "NO TRADE", "instrument": "XAU/USD",
                "profile": (profile or {}).get("profile"),
                "reason": (profile or {}).get("reason", "no directional profile")}

    stop_dist = sl_pips * GOLD_PIP                       # e.g. 200 pips × $0.10 = $20 move
    stop = entry - stop_dist if bias == "long" else entry + stop_dist

    lot = min(size_for_risk(entry, stop, risk_usd), max_lot(balance))
    lot = max(0.01, lot)
    risk_actual = round(lot * stop_dist * GOLD_USD_PER_POINT, 2)

    tps = targets(entry, stop, bias, rr)
    for t in tps:
        t["usd"] = round(lot * abs(t["price"] - entry) * GOLD_USD_PER_POINT, 2)
    be = breakeven_price(entry, tps[0]["price"]) if tps else None

    gate = evaluate_trade(balance, risk_actual, tier=tier)

    return {
        "signal": bias.upper(),
        "instrument": "XAU/USD",
        "profile": profile.get("profile"),
        "profile_id": profile.get("profile_id"),
        "justification": profile.get("reason"),
        "zone": profile.get("zone"),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "sl_pips": sl_pips,
        "lot": lot,
        "risk_usd": risk_actual,
        "targets": tps,                                  # TP1/2/3 with $ at this lot
        "breakeven": be,
        "gate": gate,
        "balance": balance,
        "tier": tier,
    }


def format_trend_lines(sig: dict) -> str:
    """Render the trend-extension TP ladder for a card (empty string if absent).

    Shared by the daily gold card, the intraday gold card, and the forex cards so
    every instrument prints its projected targets identically. Pure — reads only
    ``sig['trend_targets']`` (produced by services.structure_service.trend_targets).
    """
    tl = sig.get("trend_targets")
    if not tl or not tl.get("targets"):
        return ""
    tps = "  ·  ".join(
        f"{t['label']} {t['price']:g} ({t['ratio']:g}·{t['pct']:+.1f}%)"
        for t in tl["targets"])
    out = f"\n🎯 Trend TPs: {tps}"
    mm = tl.get("measured_move")
    if mm:
        out += f"\n📐 measured move (1.0) {mm:g}  ·  src {tl.get('source')}"
    return out


def format_card(sig: dict) -> str:
    """Telegram card in the requested gold format."""
    if sig.get("signal") == "NO TRADE":
        return (f"⏸️ *GOLD — no trade*\n_{sig.get('profile') or 'no profile'}: "
                f"{sig.get('reason', '')}_")
    arrow = "🟢 LONG" if sig["signal"] == "LONG" else "🔴 SHORT"
    lines = [
        f"📈 *GOLD XAU/USD {arrow}* @ `{sig['entry']}`",
        f"_Profile: {sig['profile']} ({sig['signal'].lower()})_",
        f"_{sig.get('justification', '')}_",
        "",
        (f"Lot: `{sig['lot']}`  ·  Risk: `${sig['risk_usd']}`  ·  SL: `{sig['stop']}`"
         + (f" ({int(sig['sl_pips'])}p)" if sig.get('sl_pips')
            else f" (Δ{sig.get('stop_distance', '')})")),
    ]
    for i, t in enumerate(sig.get("targets", []), start=1):
        lines.append(f"TP{i}  `{t['price']}`  (1:{t['rr']} · ${t['usd']})")
    if sig.get("breakeven") is not None:
        lines.append(f"BE → `{sig['breakeven']}` at 50% to TP1")
    g = sig.get("gate", {})
    lines += ["", ("✅ GATE: " + g.get("reason", "")) if g.get("allow")
              else ("⛔ GATE: " + g.get("reason", ""))]
    return "\n".join(lines) + format_trend_lines(sig)
