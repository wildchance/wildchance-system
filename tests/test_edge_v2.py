import asyncio
import datetime as dt

import pytest

from services import council, edge_v2, macro_surprise as ms


def run(c):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(c)
    finally:
        loop.close()


def test_classify_and_numbers():
    assert ms.classify("Non-Farm Employment Change") == "nfp" and ms.classify("ADP Non-Farm Employment Change") == "adp"
    assert ms.classify("Unemployment Rate") == "unemp" and ms.classify("Unemployment Claims") == "claims"
    assert ms.classify("Initial Jobless Claims") == "claims" and ms.classify("JOLTS Job Openings") == "jolts"
    assert ms.classify("ISM Manufacturing PMI") == "mpmi" and ms.classify("ISM Services PMI") == "spmi"
    assert ms.classify("Core PCE Price Index m/m") == "pce" and ms.classify("CPI y/y") == "cpi"
    assert ms.classify("Federal Funds Rate") == "rate" and ms.classify("Official Bank Rate") == "rate"
    assert ms.classify("FOMC Member Speaks") is None and ms.classify("GDP q/q") == "gdp"
    assert ms.num("215K") == 215000 and ms.num("-0.3%") == -0.3 and ms.num("1.2M") == 1.2e6 and ms.num("") is None


def test_surprise_scoring():
    assert ms.surprise("cpi", 3.1, 2.9, 2.8) == 2          # +0.2 pp = big beat
    assert ms.surprise("cpi", 2.95, 2.9, None) == 1
    assert ms.surprise("unemp", 4.4, 4.3, 4.3) == -1       # higher unemployment = miss
    assert ms.surprise("claims", 200000, 230000, None) == 2  # 13 % fewer claims = big beat
    assert ms.surprise("nfp", 50000, 100000, None) == -2
    assert ms.surprise("rate", 4.25, 4.25, 4.50) == -1     # expected cut
    assert ms.surprise("rate", 4.00, 4.25, 4.50) == -2     # bigger cut than expected
    assert ms.surprise("gdp", 1.0, None, None) == 0


def test_apply_remembers_newest_and_index():
    ev = [{"ccy": "USD", "title": "CPI y/y", "when": "2026-11-12T13:30", "actual": "3.1%", "forecast": "2.9%", "previous": ""},
          {"ccy": "USD", "title": "CPI y/y", "when": "2026-10-10T13:30", "actual": "2.0%", "forecast": "2.9%", "previous": ""},
          {"ccy": "EUR", "title": "German Manufacturing PMI", "when": "2026-11-21T08:30", "actual": "47.0",
           "forecast": "49.0", "previous": "48"},
          {"ccy": "USD", "title": "Non-Farm Employment Change", "when": "2026-11-06T13:30", "actual": "",
           "forecast": "100K", "previous": ""}]
    st, n = ms.apply({}, ev)
    assert n == 2 and st["USD"]["cpi"]["score"] == 2 and st["EUR"]["mpmi"]["score"] == -2 and "nfp" not in st["USD"]
    assert ms.category_scores(st, "USD")["inflation"] == 2
    assert ms.surprise_index(st, "EUR", now=dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc)) == -100


def test_macro_mapping_pairs_metals_indices():
    st = {"USD": {"cpi": {"score": 2}, "nfp": {"score": 1}}, "EUR": {"cpi": {"score": -1}}}
    assert edge_v2.macro_for("EUR/USD", st)["cpi"] == -2 and edge_v2.macro_for("EUR/USD", st)["nfp"] == -1
    assert edge_v2.macro_for("XAU/USD", st)["cpi"] == -2 and edge_v2.macro_for("XAU/USD", st)["nfp"] == -1
    assert edge_v2.macro_for("NAS100", st)["cpi"] == -2 and edge_v2.macro_for("NAS100", st)["nfp"] == 1


def test_row_score_bias_and_carry():
    r = edge_v2.row("EUR/USD", {"trend_d1": 1, "trend_h4": 1, "seasonality": 1}, {"cot": 2, "crowd": 2},
                    {"cpi": 2, "nfp": 2, "gdp": 1}, None)
    assert r["score"] == 10 and r["raw"] == 12 and r["bias"] == "Very Bullish" and r["categories"]["growth"] == 1
    assert edge_v2.bias(-4) == "Bearish" and edge_v2.bias(0) == "Neutral"
    c = edge_v2.carry("AUD/JPY", {"RBA": 3.6, "BOJ": 0.5})
    assert c == {"base_rate": 3.6, "quote_rate": 0.5, "diff": 3.1, "earns": "BUY"}
    up = [1.0 + i * 0.01 for i in range(60)]
    assert edge_v2.trend_vote(up) == 1 and edge_v2.trend_vote(up[::-1]) == -1 and edge_v2.trend_vote(up[:10]) == 0


def test_sentiment_from_strength_legs():
    comp = {"cot": {"EUR": 0.8, "USD": -0.4}, "retail": {"EUR": -0.5, "USD": 0.5}}
    assert edge_v2.sentiment_for("EUR/USD", comp, None) == {"cot": 2, "crowd": -2}
    assert edge_v2.sentiment_for("US30", comp, None) == {"cot": 0, "crowd": 0}


def test_council_fundamentals_vote():
    assert council.vote_fundamentals({"score": 6, "bias": "Bullish"}, "BUY")["vote"] == 1
    assert council.vote_fundamentals({"score": 6, "bias": "Bullish"}, "SELL")["vote"] == -1
    assert council.vote_fundamentals({"score": 2}, "BUY")["vote"] == 0
    assert council.vote_fundamentals(None, "BUY")["vote"] == 0


def test_text_renders():
    b = {"rows": [edge_v2.row("EUR/USD", {"trend_d1": -1}, {"cot": -2}, {"cpi": -1},
                              {"base_rate": 2.15, "quote_rate": 4.5, "diff": -2.35, "earns": "SELL"})],
         "surprise_index": {"USD": 40, "EUR": None}}
    t = edge_v2.text(b)
    assert "EUR/USD -4 Bearish" in t and "USD +40" in t
    assert "earns on SELL" in edge_v2.carry_text(b)


def test_trial_never_cuts_paid_time(monkeypatch):
    from services import billing_service as bs

    class U:
        paid_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=20)

    async def user(db, email):
        return U()

    class DB:
        async def commit(self):
            pass
    monkeypatch.setattr(bs, "_user", user)
    r = run(bs.start_trial(DB(), "a@b.co", "trader", 7, "tg:1"))
    assert r == {"ok": False, "reason": "this email already has an active plan"}
    assert run(bs.start_trial(DB(), "a@b.co", "scout", 7, "tg:1"))["ok"] is False
