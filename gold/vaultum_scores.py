"""VAULTUM feature-score board — the institutional indicator layer (Phase 5 + 7).

The connective tissue: it takes the raw reads the system already computes (DXY regime,
macro cycle, volatility regime, COT positioning, market stress, Venom AMD phase) and
distils them into a set of STANDARDISED 0-100 scores, then blends them into one
explainable GOLD BIAS & CONVICTION read that every Autobot can gate on.

Design contract — every score is an *envelope* (Phase 7 explainability):

    {"value": 0-100, "confidence": 0.0-1.0, "drivers": [...], "explanation": str}

Convention: each score is expressed as *bullish-for-gold* on a 0-100 axis
(50 = neutral, >50 = supportive of higher gold, <50 = pressure on gold). A missing
input degrades that score to neutral with low confidence — never a crash. Pure +
deterministic: the route feeds live values; this module does the maths.
"""

from __future__ import annotations

from typing import List, Optional, Dict


def _envelope(value: float, confidence: float, drivers: List[str], explanation: str) -> dict:
    return {
        "value": round(max(0.0, min(100.0, value)), 1),
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "drivers": drivers,
        "explanation": explanation,
    }


def _neutral(reason: str) -> dict:
    return _envelope(50.0, 0.1, [], f"no live input — neutral ({reason})")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --- individual institutional scores (each bullish-for-gold on 0-100) --------------

def dollar_strength_score(regime: Optional[str] = None,
                          strength_pct: Optional[float] = None) -> dict:
    """USD strength → INVERSE gold pressure. A strong dollar scores LOW (bearish gold)."""
    if regime is None and strength_pct is None:
        return _neutral("dollar")
    base = None
    drivers = []
    if strength_pct is not None:                    # 0-100 dollar strength → invert
        base = 100.0 - float(strength_pct)
        drivers.append(f"DXY strength {strength_pct:.0f}/100")
    reg = (regime or "").lower()
    if "strong" in reg or reg == "bull":
        base = 25.0 if base is None else (base + 25.0) / 2
        drivers.append("dollar regime strong → gold pressure")
    elif "weak" in reg or reg == "bear":
        base = 75.0 if base is None else (base + 75.0) / 2
        drivers.append("dollar regime weak → gold support")
    elif base is None:
        base = 50.0
        drivers.append("dollar regime neutral")
    conf = 0.8 if (regime and strength_pct is not None) else 0.55 if regime else 0.5
    return _envelope(base, conf, drivers,
                     "inverse USD: strong dollar caps gold, weak dollar lifts it")


def risk_appetite_score(state: Optional[str] = None,
                        equity_change_pct: Optional[float] = None) -> dict:
    """Risk-on vs risk-off. Gold is a partial safe haven: risk-OFF is mildly supportive,
    but a strong risk-ON melt-up can pull flows out of gold."""
    if state is None and equity_change_pct is None:
        return _neutral("risk appetite")
    drivers, base = [], 50.0
    st = (state or "").lower()
    if "off" in st or st == "fear":
        base = 62.0
        drivers.append("risk-off → safe-haven bid")
    elif "on" in st or st == "greed":
        base = 42.0
        drivers.append("risk-on → flows compete with gold")
    if equity_change_pct is not None:
        # a sharp equity selloff (risk-off) nudges gold up
        base += max(-12.0, min(12.0, -float(equity_change_pct) * 2.0))
        drivers.append(f"equities {equity_change_pct:+.1f}%")
    conf = 0.65 if state else 0.45
    return _envelope(base, conf, drivers, "gold as partial haven — risk-off supportive")


def liquidity_score(level: Optional[float] = None, direction: Optional[str] = None) -> dict:
    """Global liquidity impulse. Expanding liquidity (easy money) is bullish gold."""
    if level is None and direction is None:
        return _neutral("liquidity")
    drivers, base = [], 50.0
    if level is not None:
        base = float(level)
        drivers.append(f"liquidity level {level:.0f}/100")
    d = (direction or "").lower()
    if d in ("expanding", "easing", "up"):
        base = max(base, 65.0); drivers.append("liquidity expanding → bullish gold")
    elif d in ("contracting", "tightening", "down"):
        base = min(base, 35.0); drivers.append("liquidity tightening → bearish gold")
    return _envelope(base, 0.5 if (level is not None or direction) else 0.1, drivers,
                     "expanding liquidity supports hard assets")


def inflation_pressure_score(direction: Optional[str] = None,
                             real_rate_direction: Optional[str] = None) -> dict:
    """Inflation & REAL rates. Rising inflation with falling real rates is the classic
    bullish-gold regime; rising real rates are gold's biggest headwind."""
    if direction is None and real_rate_direction is None:
        return _neutral("inflation")
    drivers, base = [], 50.0
    d = (direction or "").lower()
    if d in ("rising", "up", "hot"):
        base += 10; drivers.append("inflation rising")
    elif d in ("falling", "down", "cooling"):
        base -= 8; drivers.append("inflation cooling")
    rr = (real_rate_direction or "").lower()
    if rr in ("falling", "down", "negative"):
        base += 18; drivers.append("real rates falling → strong gold tailwind")
    elif rr in ("rising", "up", "positive"):
        base -= 18; drivers.append("real rates rising → gold headwind")
    conf = 0.75 if real_rate_direction else 0.5 if direction else 0.1
    return _envelope(base, conf, drivers, "real-rate direction is gold's dominant macro driver")


def market_stress_score(vix: Optional[float] = None,
                        dollar_regime: Optional[str] = None) -> dict:
    """Systemic stress. Elevated stress (high VIX) is a safe-haven bid for gold — unless
    it comes with a violent dollar bid, which competes."""
    if vix is None:
        return _neutral("market stress")
    drivers = [f"VIX {vix:.0f}"]
    # VIX 12 (calm) → ~45, VIX 20 → ~58, VIX 30+ → ~72
    base = 45.0 + max(0.0, (float(vix) - 12.0)) * 1.6
    if float(vix) >= 28:
        drivers.append("high stress → safe-haven bid")
    if (dollar_regime or "").lower().find("strong") >= 0 and float(vix) >= 25:
        base -= 8; drivers.append("but strong dollar competes for the haven bid")
    return _envelope(base, 0.6, drivers, "stress lifts gold as a haven, dollar can offset")


def vol_regime_score(regime: Optional[str] = None, atr_pct: Optional[float] = None) -> dict:
    """Volatility REGIME — not directional, a conviction/size modifier. Expansion regimes
    mean trends run; compression means chop. Scored around 50 (neutral direction) with the
    signal carried in the confidence + drivers so the risk engine can size off it."""
    if regime is None and atr_pct is None:
        return _neutral("vol regime")
    drivers, base = [], 50.0
    r = (regime or "").lower()
    if "expan" in r or "high" in r:
        drivers.append("volatility expansion — trends run, size up on confirmation")
    elif "low" in r or "compress" in r:
        drivers.append("compression — chop risk, wait for the break")
    if atr_pct is not None:
        drivers.append(f"ATR percentile {atr_pct*100:.0f}%")
    return _envelope(base, 0.5, drivers, "regime modifies conviction/size, not direction")


def macro_cycle_score(gold_bias: Optional[str] = None,
                      conviction: Optional[str] = None) -> dict:
    """The system's own macro-cycle read (macro_cycle.regime_read) folded in directly."""
    if gold_bias is None:
        return _neutral("macro cycle")
    drivers = [f"macro cycle {gold_bias}"]
    gb = gold_bias.lower()
    base = 68.0 if gb in ("bullish", "long", "up") else 32.0 if gb in ("bearish", "short", "down") else 50.0
    conf = {"high": 0.85, "medium": 0.6, "low": 0.4}.get((conviction or "").lower(), 0.55)
    if conviction:
        drivers.append(f"{conviction} conviction")
    return _envelope(base, conf, drivers, "the platform's macro-regime gold bias")


def venom_phase_score(venom: Optional[dict] = None) -> dict:
    """Venom AMD phase → the timing/conviction overlay. Manipulation windows are where the
    reversal sets up; distribution is the trend run. Not net-directional on its own."""
    if not venom:
        return _neutral("venom phase")
    conf_map = {"high": 0.8, "medium": 0.6, "low": 0.4}
    c = (venom.get("confluence") or {})
    phase = (venom.get("intraday") or {}).get("phase", "?")
    conviction = c.get("conviction", "low")
    drivers = [f"AMD {phase}", f"{c.get('timeframes_aligned', 0)} TFs aligned"]
    if c.get("htf_manipulation_window"):
        drivers.append("HTF manipulation window ⚠️")
    return _envelope(50.0, conf_map.get(conviction, 0.4), drivers,
                     "AMD phase overlays timing + conviction on the directional scores")


def jpy_liquidity_score(jpy_usd_roc_30d: Optional[float] = None) -> dict:
    """JPY carry / global-liquidity signal (WorldMonitor-style). USD/JPY 30-day ROC:
    yen STRENGTHENING (USDJPY falling, ROC < 0) = carry unwind = risk-off = BULLISH gold;
    yen weakening (ROC > 0) = carry-on risk appetite = mild pressure on gold."""
    if jpy_usd_roc_30d is None:
        return _neutral("JPY carry")
    r = float(jpy_usd_roc_30d)
    # ROC -4% → ~72 (unwind, gold bid); +4% → ~34; clamp
    base = _clamp(50.0 - r * 5.0, 0.0, 100.0)
    drivers = [f"USD/JPY 30d {r:+.1f}%"]
    if r <= -2:
        drivers.append("yen carry UNWIND → risk-off, gold bid")
    elif r >= 2:
        drivers.append("yen weak / carry-on → risk-on")
    return _envelope(base, 0.6, drivers,
                     "yen-carry unwind lifts gold; carry-on competes with it")


def geopolitical_score(risk_0_100: Optional[float] = None) -> dict:
    """Geopolitical-risk → safe-haven bid. Higher global conflict tone = more support
    for gold. Scored above 50 as risk rises (a haven tailwind, not a directional macro)."""
    if risk_0_100 is None:
        return _neutral("geopolitical")
    g = _clamp(float(risk_0_100), 0.0, 100.0)
    base = _clamp(50.0 + (g - 50.0) * 0.4, 0.0, 100.0)   # damped haven tilt
    drivers = [f"geopolitical risk {g:.0f}/100"]
    if g >= 65:
        drivers.append("elevated conflict tone → safe-haven bid")
    return _envelope(base, 0.5, drivers, "geopolitical stress is a gold haven catalyst")


def cb_divergence_score(fed_minus_peers: Optional[float] = None) -> dict:
    """Central-bank policy divergence (BIS-style). Fed policy rate vs the G10 peer average
    (percentage points): Fed TIGHTER than peers = strong dollar = BEARISH gold; Fed easier
    = weak dollar = BULLISH gold."""
    if fed_minus_peers is None:
        return _neutral("CB divergence")
    d = float(fed_minus_peers)
    base = _clamp(50.0 - d * 6.5, 0.0, 100.0)     # +2pp Fed premium → ~37; -2pp → ~63
    drivers = [f"Fed − peer avg {d:+.2f}pp"]
    if d >= 1.0:
        drivers.append("Fed hawkish vs peers → dollar bid, gold headwind")
    elif d <= -1.0:
        drivers.append("Fed dovish vs peers → dollar soft, gold tailwind")
    return _envelope(base, 0.55, drivers, "policy-rate divergence drives the dollar vs gold")


# --- the composite board ----------------------------------------------------------

# Directional scores carry the bias; regime/phase scores carry conviction only.
_DIRECTIONAL = {
    "dollar_strength": 0.22,
    "macro_cycle": 0.20,
    "inflation_pressure": 0.16,
    "cb_divergence": 0.10,
    "market_stress": 0.10,
    "geopolitical": 0.08,
    "jpy_liquidity": 0.06,
    "risk_appetite": 0.05,
    "liquidity": 0.03,
}
_CONVICTION_ONLY = ("vol_regime", "venom_phase")


def gold_bias_board(scores: Dict[str, dict]) -> dict:
    """Blend the standardised scores into one GOLD BIAS & CONVICTION read.

    Direction comes from the confidence-weighted directional scores; conviction blends
    the agreement of those scores with the regime/phase overlays. Fully explainable —
    every contributing driver is surfaced."""
    num = den = 0.0
    contribs = []
    for key, weight in _DIRECTIONAL.items():
        env = scores.get(key)
        if not env:
            continue
        w = weight * env["confidence"]
        num += env["value"] * w
        den += w
        contribs.append({"score": key, "value": env["value"],
                         "confidence": env["confidence"], "weight": round(w, 3)})
    bias_value = (num / den) if den else 50.0

    # conviction: how strongly the directional scores agree, lifted/damped by overlays
    spread = 0.0
    if contribs:
        vals = [c["value"] for c in contribs]
        spread = sum(abs(v - 50) for v in vals) / len(vals)      # avg distance from neutral
    overlay_conf = [scores[k]["confidence"] for k in _CONVICTION_ONLY if k in scores]
    overlay = (sum(overlay_conf) / len(overlay_conf)) if overlay_conf else 0.5
    conviction_pct = round(min(100.0, spread * 2.0 * (0.6 + 0.4 * overlay)), 0)

    if bias_value >= 56:
        direction, tag = "long", "🟢 BULLISH gold"
    elif bias_value <= 44:
        direction, tag = "short", "🔴 BEARISH gold"
    else:
        direction, tag = "neutral", "⚪ NEUTRAL / two-way"

    mean_conf = round(sum(c["confidence"] for c in contribs) / len(contribs), 2) if contribs else 0.1
    top = sorted(contribs, key=lambda c: c["weight"], reverse=True)[:3]
    top_drivers = []
    for c in top:
        top_drivers += (scores.get(c["score"], {}).get("drivers") or [])[:1]

    return {
        "gold_bias": round(bias_value, 1),
        "direction": direction,
        "conviction_pct": conviction_pct,
        "confidence": mean_conf,
        "tag": tag,
        "top_drivers": top_drivers,
        "components": scores,
        "weighting": {"directional": _DIRECTIONAL, "conviction_only": list(_CONVICTION_ONLY)},
        "explanation": (
            f"{tag} — bias {bias_value:.0f}/100, conviction {conviction_pct:.0f}% "
            f"(confidence {mean_conf:.0%}). "
            + ("; ".join(top_drivers) if top_drivers else "insufficient live inputs")),
    }


def format_board(board: dict) -> str:
    """Telegram/console line for the composite board."""
    c = board
    bars = int(round(c["gold_bias"] / 10))
    meter = "█" * bars + "░" * (10 - bars)
    return (f"🏛️ *VAULTUM — Gold Bias*  {c['tag']}\n"
            f"   bias {c['gold_bias']:.0f}/100 [{meter}]  ·  conviction {c['conviction_pct']:.0f}%  ·  "
            f"conf {c['confidence']:.0%}\n"
            f"   {('; '.join(c['top_drivers']) if c['top_drivers'] else '—')}")
