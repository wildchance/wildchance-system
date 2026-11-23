import datetime as dt
import lzma
import struct

from propfirm import target_lots as tl
from services import council as cn
from services import cycles
from services import data_feeds as df


def test_vix_agent_risk_off_and_risk_on():
    stress = {"close": 35.9, "regime": "stress"}
    assert cn.vote_vix(stress, "XAU/USD", "BUY")["vote"] == 1          # safe haven
    assert cn.vote_vix(stress, "XAU/USD", "SELL")["vote"] == -1
    assert cn.vote_vix(stress, "USD/JPY", "SELL")["vote"] == 1         # JPY bid
    assert cn.vote_vix(stress, "NAS100", "BUY")["vote"] == -1          # risk asset
    assert cn.vote_vix(stress, "BTC/USD", "SELL")["vote"] == 1
    assert cn.vote_vix(stress, "EUR/USD", "BUY")["vote"] == 0          # no view
    calm = {"close": 13.0, "regime": "calm"}
    assert cn.vote_vix(calm, "NAS100", "BUY")["vote"] == 1 and cn.vote_vix(calm, "NAS100", "SELL")["vote"] == 0
    assert cn.vote_vix({"close": 19, "regime": "normal"}, "NAS100", "BUY")["vote"] == 0


def test_agent_scoring_mutes_unreliable_agents():
    cases = ([{"outcome": "broke", "votes": {"news": 1, "cot": -1}}] * 8
             + [{"outcome": "held", "votes": {"news": -1, "cot": 1}}] * 2
             + [{"outcome": "pending", "votes": {"news": 1}}])
    sc = cn.agent_scores(cases)
    assert sc["news"]["n"] == 10 and sc["news"]["right"] == 0 and sc["news"]["muted"]
    assert sc["cot"]["right"] == 10 and not sc["cot"]["muted"]
    v = cn.apply_scores([{"agent": "news", "vote": 1, "why": "x"}, {"agent": "cot", "vote": 1, "why": "y"}], sc)
    assert v[0]["vote"] == 0 and v[0]["muted"] and v[1]["vote"] == 1 and v[1]["accuracy"] > 0.9


def test_stock_and_index_lots():
    assert tl.pip_size("NAS100") == 0.1 and tl.pip_value_per_lot("NAS100") == 0.1
    assert tl.pip_size("NVDA") == 0.01 and tl.pip_value_per_lot("NVDA") == 0.01
    assert tl.card_lines("NAS100", 25000.0, 24950.0, [{"name": "TP1", "price": 25100.0}], balances=[5000])


def test_cycles():
    bars, d, p = [], dt.date(2009, 1, 2), 100.0
    while d < dt.date(2026, 11, 20):
        if d.weekday() < 5:
            p *= 1.0004 if d.year % 4 == 2 else 0.9999
            bars.append((d, p, p * 1.001, p * 0.999, p))
        d += dt.timedelta(days=1)
    c = cycles.read(bars, dt.date(2026, 11, 23))
    pr = c["presidential"]
    assert pr["now"]["term_year"] == 2 and pr["now"]["name"] == "midterm" and pr["now"]["pct_up"] == 100
    assert {h["cycle"] for h in c["hurst"]} == {"20d", "40d", "80d", "20w", "40w"}
    assert cycles.year_in_term(2028) == 4 and cycles.card_line(c).startswith("🗳 midterm year")


def test_dukascopy_candle_decoder():
    def rec(sec, o, c, lo, hi, v):
        return struct.pack(">IIIIIf", sec, o, c, lo, hi, v)
    raw = lzma.compress(rec(0, 109900, 110100, 109800, 110200, 5.0) + rec(86400, 0, 0, 0, 0, 0.0)
                        + rec(2 * 86400, 110100, 109950, 109900, 110300, 3.0), format=lzma.FORMAT_ALONE)
    bars = df.parse_duka_candles(raw, 2025, 1e-5)
    assert bars == [(dt.date(2025, 1, 1), 1.099, 1.102, 1.098, 1.101),
                    (dt.date(2025, 1, 3), 1.101, 1.103, 1.099, 1.0995)]            # empty day skipped
    assert df.parse_duka_candles(b"not lzma", 2025, 1e-5) == []
    assert df.duka_point("USD/JPY") == 1e-3 and df.duka_point("EUR/USD") == 1e-5
