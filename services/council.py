"""COUNCIL — the multi-agent war room behind every sniper shot (CENTCOM J2 cell).

MiroFish (external multi-agent simulation) needs its own server + LLM budget and has
been dormant; this council runs the same idea in-process, free and instant: each
agent reads ONE intelligence source from cache and votes on a proposed side.

  cot        COT + retail confluence verdict for the pair (wildchance feed)
  sentiment  news (NVIDIA) + Polymarket + retail-contrarian composite
  weekly     ICT weekly profile (with the prior-week fallback)
  structure  yearly CBDR: swing leg (OTE side) and the magnet-mean side
  history    case library: how the zone's roles have held recently
  macro      owner's regime / manual locks (BUY_ONLY / SELL_ONLY)
  news       Alpha Vantage + MarketAux news sentiment (refreshed 6-hourly, cache only)
  vix        CBOE VIX regime — stress (≥25) backs gold / JPY / CHF, votes against
             indices, crypto, stocks, AUD/NZD; calm (<16) backs risk-on trades
  (agents proven wrong on resolved live shots — < AGENT_MUTE_BELOW over ≥ AGENT_MUTE_MIN_N
   votes — are muted: their vote counts as an abstain)
  base_rates the asset's long-term lean from decades of yearly boxes (gold: bullish);
             blocks only POSITION trades against it — swing trades may still fade it
  mirofish   external MiroFish score when MIROFISH_BASE_URL is live (else abstains)

Votes: +1 for the side, −1 against, 0 abstain. A LOCK (macro) against the side is a
veto. verdict: GO needs ≥ COUNCIL_MIN_FOR (4) agents FOR, ≤ COUNCIL_MAX_AGAINST (0)
against and no veto (owner's rule) · HOLD — re-checked every hour, sent once promoted
to GO · VETO.
"""

from __future__ import annotations

import math

import datetime as dt
from typing import Dict, List, Optional

from decouple import config

MIN_SCORE = int(config("COUNCIL_MIN_SCORE", default="1"))
MIN_FOR = int(config("COUNCIL_MIN_FOR", default="4"))          # owner: 4 verified agents
MAX_AGAINST = int(config("COUNCIL_MAX_AGAINST", default="0"))  # …and none against


def _v(agent: str, vote: int, why: str) -> Dict:
    return {"agent": agent, "vote": vote, "why": why}


def vote_cot(row: Optional[Dict], side: str) -> Dict:
    if not row:
        return _v("cot", 0, "no COT/retail read")
    want = "LONG" if side == "BUY" else "SHORT"
    v, conf = row.get("verdict"), row.get("confidence") or 0
    if v not in ("LONG", "SHORT") or conf < 60:
        return _v("cot", 0, f"{v} conf {conf}")
    return _v("cot", 1 if v == want else -1, f"{v} conf {conf}"
              + (" · COT agrees" if row.get("cot_agrees") else ""))


def vote_sentiment(read: Optional[Dict], side: str) -> Dict:
    comp = (read or {}).get("composite")
    if comp is None or abs(comp) < 0.2:
        return _v("sentiment", 0, f"composite {comp}")
    up = comp > 0
    return _v("sentiment", 1 if up == (side == "BUY") else -1, f"composite {comp:+.2f}")


def vote_weekly(read: Optional[Dict], side: str) -> Dict:
    from gold.weekly import confluence
    v = confluence(read, side)
    tag = (read or {}).get("profile") or "none"
    return _v("weekly", {"confirms": 1, "diverges": -1}.get(v, 0), f"{v} ({tag})")


def vote_structure(yr: Optional[Dict], side: str) -> Dict:
    if not yr or not yr.get("ok"):
        return _v("structure", 0, "no yearly read")
    score, why = 0, []
    o = yr.get("ote")
    if o:
        score += 1 if o["side"] == side else -1
        why.append(f"{o['leg']} leg")
    mg = yr.get("magnet")
    if mg:                                   # price below the magnet mean pulls up, above pulls down
        pull = "BUY" if mg["side"] == "below" else "SELL"
        score += 1 if pull == side else 0
        why.append(f"magnet {mg['year']} {mg['side']}")
    return _v("structure", max(-1, min(1, score)), ", ".join(why) or "neutral")


def vote_history(role_stats: Optional[Dict]) -> Dict:
    if not role_stats or role_stats.get("n", 0) < 3:
        return _v("history", 0, "too few cases")
    r = role_stats["recent_rate"]
    return _v("history", 1 if r >= 0.6 else -1 if r < 0.45 else 0,
              f"{role_stats['role']} held {role_stats['held']}/{role_stats['n']} (recent {r:.0%})")


def vote_macro(lock: Optional[str], side: str) -> Dict:
    if not lock or lock == "BOTH":
        return _v("macro", 0, "no lock")
    ok = (lock == "BUY_ONLY" and side == "BUY") or (lock == "SELL_ONLY" and side == "SELL")
    return {**_v("macro", 1 if ok else -1, lock), "veto": not ok}


def vote_news(n: Optional[Dict], side: str) -> Dict:
    """Alpha Vantage + MarketAux news sentiment (−1..+1; AV calls ≥0.15 bullish)."""
    if not n or n.get("score") is None:
        return _v("news", 0, "no recent news read")
    s = n["score"]
    if abs(s) < 0.15:
        return _v("news", 0, f"neutral {s:+.2f}")
    return _v("news", 1 if (s > 0) == (side == "BUY") else -1, f"{s:+.2f} ({', '.join(n.get('sources') or {})})")


def vote_base_rates(history: Optional[Dict], side: str, trade_type: str = "swing") -> Dict:
    """The asset's long-term lean from its yearly base rates (e.g. gold: 50 % of years
    close above the Q1 box, 22 % below → BUY lean). Agreeing trades get +1; against
    the lean only a POSITION trade gets −1 — swing trades can still fade it."""
    from services.yearly_cbdr import lean
    ln = lean(history)
    if not ln:
        return _v("base_rates", 0, "no long-term lean" if history else "no history")
    why = (f"{ln} lean ({history.get('years')}y: {history.get('pct_close_above_box')}% close above box, "
           f"{history.get('pct_close_below_box')}% below)")
    if ln == side:
        return _v("base_rates", 1, why)
    return _v("base_rates", -1 if trade_type == "position" else 0, why + " — against")


SAFE_HAVEN_BUY = ("XAU", "XAG")                      # BUY these in stress
SAFE_HAVEN_QUOTE = ("JPY", "CHF")                    # USDJPY / USDCHF: SELL in stress
RISK_ASSETS = ("NAS100", "US30", "SPX", "US500", "GER40", "UK100", "JP225",
               "BTC", "ETH", "DOGE", "SOL", "XRP", "ADA", "AUD", "NZD")


def risk_side(symbol: str) -> Optional[str]:
    """Which side a RISK-OFF (stress) market favours for this symbol, if any."""
    s = symbol.upper().replace("/", "")
    if s.startswith(SAFE_HAVEN_BUY):
        return "BUY"
    if len(s) == 6 and s.isalpha() and s[3:] in SAFE_HAVEN_QUOTE:
        return "SELL"                                # USD/JPY, EUR/JPY, … fall when JPY is bid
    if s.startswith(RISK_ASSETS) or (s.isalpha() and len(s) <= 5):
        return "SELL"                                # indices, crypto, AUD/NZD, single stocks
    return None


def vote_vix(vix: Optional[Dict], symbol: str, side: str) -> Dict:
    """VIX regime: stress (≥25) backs safe havens and votes AGAINST risk-on trades;
    calm (<16) backs risk-on trades only; normal abstains."""
    if not vix or vix.get("close") is None:
        return _v("vix", 0, "no VIX read")
    stress_side = risk_side(symbol)
    reg, lvl = vix.get("regime"), vix["close"]
    if not stress_side or reg == "normal":
        return _v("vix", 0, f"VIX {lvl:.1f} {reg}")
    if reg == "stress":
        return _v("vix", 1 if side == stress_side else -1, f"VIX {lvl:.1f} stress → risk-off")
    risk_on = "BUY" if stress_side == "SELL" else "SELL"
    return _v("vix", 1 if side == risk_on else 0, f"VIX {lvl:.1f} calm → risk-on")


def midterm_window(today: dt.date) -> bool:
    """Fisher's 'Midterm Miracle' window: Q4 of the midterm year → end of Q2 next year."""
    return (today.month >= 10 and today.year % 4 == 2) or (today.month <= 6 and (today.year - 1) % 4 == 2)


def vote_seasonal(symbol: str, side: str, today: Optional[dt.date] = None) -> Dict:
    """Inside the midterm window, BUY on US equity indices, US stocks and BTC/ETH gets +1.
    Never votes against anything — the VIX / structure agents still decide timing."""
    today = today or dt.date.today()
    s = symbol.upper().replace("/", "")
    us_risk = s.startswith(("NAS100", "US30", "SPX", "US500", "US2000", "BTC", "ETH")) or (s.isalpha() and len(s) <= 5)
    if not midterm_window(today):
        return _v("seasonal", 0, "outside the midterm window")
    if us_risk and side == "BUY":
        return _v("seasonal", 1, "midterm window (Q4 midterm → Q2): historically the strongest 9 months")
    return _v("seasonal", 0, "midterm window — no view for this market/side")


def vote_fundamentals(edge: Optional[Dict], side: str) -> Dict:
    """EdgeFinder v2 macro + sentiment + trend score: |score| ≥ 3 votes with its sign."""
    if not edge:
        return _v("fundamentals", 0, "no EdgeFinder score yet")
    s = int(edge.get("score") or 0)
    if abs(s) < 3:
        return _v("fundamentals", 0, f"EdgeFinder {s:+d} — neutral")
    agree = (s > 0) == (side == "BUY")
    return _v("fundamentals", 1 if agree else -1, f"EdgeFinder {s:+d} {edge.get('bias', '')}")


def vote_plan(plans: Optional[List[Dict]], side: str, price: Optional[float], tol: float = 0.0) -> Dict:
    """The owner's own plan (data/owner_plans.csv): +1 when this trade matches an active
    plan zone price is in, −1 when it fights one (e.g. a BUY inside a SELL plan zone)."""
    if not plans or price is None:
        return _v("plan", 0, "no owner plan here")
    here = [p for p in plans if p["low"] - tol <= price <= p["high"] + tol]
    if not here:
        return _v("plan", 0, "no owner plan zone at price")
    p = here[0]
    return _v("plan", 1 if p["side"] == side else -1, f"owner {p['side']} plan: {p['label']}")


def vote_mirofish(m: Optional[Dict], side: str) -> Dict:
    if not m or m.get("score") is None:
        return _v("mirofish", 0, "offline")
    s = m["score"]
    if 36 <= s <= 64:
        return _v("mirofish", 0, f"neutral {s}")
    return _v("mirofish", 1 if (s >= 65) == (side == "BUY") else -1, f"score {s}")


MUTE_BELOW = float(config("AGENT_MUTE_BELOW", default="0.40"))   # accuracy floor
MUTE_MIN_N = int(config("AGENT_MUTE_MIN_N", default="8"))         # votes before judging
# Weighted council: each vote counts by the agent's MEASURED accuracy (log-odds), giving a
# probability that TP1 comes before the stop. Enforced (GO needs ≥ MIN_PROB) only once the
# graded history holds PROB_MIN_CASES resolved signals; the 4-for / 0-against floor stays.
MIN_PROB = float(config("COUNCIL_MIN_PROB", default="0.60"))
PROB_MIN_CASES = int(config("COUNCIL_PROB_MIN_CASES", default="20"))
BASE_RATE = float(config("COUNCIL_BASE_RATE", default="0.50"))


def agent_scores(cases: List[Dict]) -> Dict[str, Dict]:
    """Grade each agent on RESOLVED live shots: a +1 vote is right when the trade
    held (TP1 first), a −1 vote is right when it broke. Laplace accuracy."""
    out: Dict[str, Dict] = {}
    for c in cases:
        if c.get("outcome") not in ("held", "broke"):
            continue
        for agent, vote in (c.get("votes") or {}).items():
            if not vote:
                continue
            a = out.setdefault(agent, {"n": 0, "right": 0})
            a["n"] += 1
            a["right"] += int((vote > 0) == (c["outcome"] == "held"))
    for a in out.values():
        a["accuracy"] = round((a["right"] + 1) / (a["n"] + 2), 2)
        a["muted"] = a["n"] >= MUTE_MIN_N and a["accuracy"] < MUTE_BELOW
    return out


def probability(votes: List[Dict], scores: Optional[Dict[str, Dict]], base: float = BASE_RATE) -> Dict:
    """P(TP1 before stop) = sigmoid(logit(base) + Σ vote · logit(agent accuracy)).
    Unscored agents count at a weak 0.55; muted agents (vote 0) add nothing."""
    def logit(p):
        p = min(0.95, max(0.05, p))
        return math.log(p / (1 - p))
    x = logit(base)
    graded = 0
    for v in votes:
        if not v.get("vote"):
            continue
        s = (scores or {}).get(v["agent"])
        acc = s["accuracy"] if s and s.get("n", 0) >= 3 else 0.55
        graded = max(graded, s.get("n", 0) if s else 0)
        x += v["vote"] * logit(acc)
    p = 1 / (1 + math.exp(-x))
    return {"prob": round(p, 3), "graded": graded}


def apply_scores(votes: List[Dict], scores: Optional[Dict[str, Dict]]) -> List[Dict]:
    """Agents that have proven unreliable are muted (their vote counts as abstain)."""
    if not scores:
        return votes
    out = []
    for v in votes:
        s = scores.get(v["agent"])
        if s and s["muted"] and v["vote"]:
            out.append({**v, "vote": 0, "muted": True,
                        "why": f"{v['why']} — muted ({s['right']}/{s['n']} right)"})
        else:
            out.append({**v, **({"accuracy": s["accuracy"]} if s else {})})
    return out


def decide(votes: List[Dict]) -> Dict:
    score = sum(v["vote"] for v in votes)
    veto = [v["agent"] for v in votes if v.get("veto")]
    n_for = sum(1 for v in votes if v["vote"] > 0)
    n_against = sum(1 for v in votes if v["vote"] < 0)
    ok = score >= MIN_SCORE and n_for >= MIN_FOR and n_against <= MAX_AGAINST
    verdict = "VETO" if veto else "GO" if ok else "HOLD"
    need = None if ok or veto else (f"needs {MIN_FOR} for / ≤{MAX_AGAINST} against "
                                    f"(has {n_for} / {n_against}) — held until promoted")
    return {"verdict": verdict, "score": score, "veto": veto, "for": n_for, "against": n_against,
            "need": need, "votes": votes}


async def convene(symbol: str, side: str, yearly: Optional[Dict] = None,
                  role_stats: Optional[Dict] = None, trade_type: str = "swing",
                  scores: Optional[Dict[str, Dict]] = None) -> Dict:
    """Gather every agent's vote from cached intelligence (no paid calls)."""
    sym = symbol.upper().replace("/", "")
    pair = f"{sym[:3]}/{sym[3:6]}" if len(sym) == 6 and sym.isalpha() else symbol
    cot_row = sent = wk = lock = mf = news = vix = None
    price = (yearly or {}).get("price")
    tol = 0.0
    try:
        from services import owner_plans
        from services.yearly_cbdr import tolerance
        plans = owner_plans.active(symbol, price)
        tol = tolerance(symbol, price) if price else 0.0
    except Exception:
        plans = []
    try:
        from services.data_feeds import macro_cached
        vix = macro_cached().get("vix")
    except Exception:
        pass
    try:
        from services.data_feeds import news_sentiment
        news = news_sentiment(symbol)
        if news and sym.startswith("USD") and len(sym) == 6:
            news = {**news, "score": -news["score"]}       # AV scores the quote currency (FOREX:JPY)
    except Exception:
        pass
    try:
        from services.wildchance_service import get_latest_feed
        feed = await get_latest_feed() or {}
        cot_row = next((s for s in feed.get("signals") or [] if s.get("pair") == pair), None)
    except Exception:
        pass
    try:
        from services.market_sentiment import read
        sent = read(symbol)
    except Exception:
        pass
    try:
        from services.trade_gate import weekly_profile
        wk = await weekly_profile(symbol)
    except Exception:
        pass
    try:
        from services.swing_service import macro_bias
        lock = macro_bias().get(sym)
    except Exception:
        pass
    try:
        from services import mirofish_service as mfs
        if mfs.MIROFISH_BASE_URL:
            mf = await mfs.score(pair)
    except Exception:
        pass
    if yearly is None:
        try:
            from services.yearly_cbdr import run
            yearly = await run(symbol)
        except Exception:
            yearly = None
    edge = None
    try:
        from services import edge_v2
        edge = await edge_v2.score_for(symbol)
    except Exception:
        pass
    votes = [vote_cot(cot_row, side), vote_sentiment(sent, side), vote_weekly(wk, side),
             vote_structure(yearly, side), vote_history(role_stats), vote_macro(lock, side),
             vote_news(news, side), vote_base_rates((yearly or {}).get("history"), side, trade_type),
             vote_vix(vix, symbol, side), vote_seasonal(symbol, side),
             vote_plan(plans, side, price, tol),
             vote_mirofish(mf, side), vote_fundamentals(edge, side)]
    votes = apply_scores(votes, scores)
    d = decide(votes)
    pr = probability(votes, scores)
    cases = max((s.get("n", 0) for s in (scores or {}).values()), default=0)
    d.update(prob=pr["prob"], prob_enforced=cases >= PROB_MIN_CASES)
    if d["verdict"] == "GO" and d["prob_enforced"] and pr["prob"] < MIN_PROB:
        d.update(verdict="HOLD", need=f"weighted council P(TP1) {pr['prob']:.0%} < {MIN_PROB:.0%}")
    return {"symbol": symbol, "side": side, **d}


def summary(c: Dict) -> str:
    icon = {1: "✅", -1: "❌", 0: "▫️"}
    p = (f" · P(TP1) {c['prob']:.0%}" + ("" if c.get("prob_enforced") else " (learning)")) if c.get("prob") is not None else ""
    return (f"🏛 Council *{c['verdict']}* ({c['for']} for / {c['against']} against{p}): "
            + " ".join(f"{icon[v['vote']]}{v['agent']}" for v in c["votes"]))
