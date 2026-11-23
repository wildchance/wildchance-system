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
  base_rates the asset's long-term lean from decades of yearly boxes (gold: bullish);
             blocks only POSITION trades against it — swing trades may still fade it
  mirofish   external MiroFish score when MIROFISH_BASE_URL is live (else abstains)

Votes: +1 for the side, −1 against, 0 abstain. A LOCK (macro) against the side is a
veto. verdict: GO needs ≥ COUNCIL_MIN_FOR (4) agents FOR, ≤ COUNCIL_MAX_AGAINST (0)
against and no veto (owner's rule) · HOLD — re-checked every hour, sent once promoted
to GO · VETO.
"""

from __future__ import annotations

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


def vote_mirofish(m: Optional[Dict], side: str) -> Dict:
    if not m or m.get("score") is None:
        return _v("mirofish", 0, "offline")
    s = m["score"]
    if 36 <= s <= 64:
        return _v("mirofish", 0, f"neutral {s}")
    return _v("mirofish", 1 if (s >= 65) == (side == "BUY") else -1, f"score {s}")


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
                  role_stats: Optional[Dict] = None, trade_type: str = "swing") -> Dict:
    """Gather every agent's vote from cached intelligence (no paid calls)."""
    sym = symbol.upper().replace("/", "")
    pair = f"{sym[:3]}/{sym[3:6]}" if len(sym) == 6 and sym.isalpha() else symbol
    cot_row = sent = wk = lock = mf = news = None
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
    votes = [vote_cot(cot_row, side), vote_sentiment(sent, side), vote_weekly(wk, side),
             vote_structure(yearly, side), vote_history(role_stats), vote_macro(lock, side),
             vote_news(news, side), vote_base_rates((yearly or {}).get("history"), side, trade_type),
             vote_mirofish(mf, side)]
    return {"symbol": symbol, "side": side, **decide(votes)}


def summary(c: Dict) -> str:
    icon = {1: "✅", -1: "❌", 0: "▫️"}
    return (f"🏛 Council *{c['verdict']}* ({c['for']} for / {c['against']} against): "
            + " ".join(f"{icon[v['vote']]}{v['agent']}" for v in c["votes"]))
