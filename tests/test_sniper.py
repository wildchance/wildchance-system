import datetime as dt

from services import case_library as cl, sniper


def _b(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_cisd_buy_fires_after_sweep_and_close_through_run_open():
    bars = [_b(1.100, 1.101, 1.099, 1.1005), _b(1.1005, 1.1008, 1.0990, 1.0992),   # bearish run start
            _b(1.0992, 1.0995, 1.0975, 1.0978), _b(1.0978, 1.0980, 1.0960, 1.0965),  # sweep into band
            _b(1.0965, 1.1015, 1.0963, 1.1010)]                                      # close > 1.1005
    s = sniper.cisd(bars, "BUY", (1.0955, 1.0970))
    assert s["fired"] and s["level"] == 1.1005 and s["extreme"] == 1.0960


def test_cisd_holds_without_sweep_or_cross():
    bars = [_b(1.10, 1.101, 1.099, 1.1005), _b(1.1005, 1.1008, 1.0990, 1.0992),
            _b(1.0992, 1.0995, 1.0985, 1.0988), _b(1.0988, 1.0999, 1.0986, 1.0998)]
    s = sniper.cisd(bars, "BUY", (1.0955, 1.0970))
    assert not s["fired"] and s["why"] == "no sweep into the zone"


def test_roles_and_candidates():
    assert sniper.roles_of(["2025 1", "⚓Apr-Jul-25 box 4", "FOMC hold Oct-28 -3.5"]) == \
        ["range high", "extreme 3 · TP4", "extreme 2 · TP3"]
    r = {"ok": True, "symbol": "EUR/USD",
         "clusters": [{"low": 1.0733, "high": 1.0754, "mid": 1.0744, "members": ["2024 0", "2023 0.5"]}]}
    bars = [_b(1.09, 1.091, 1.089, 1.09)] * 20 + [_b(1.08, 1.081, 1.074, 1.076)] * 12
    c = sniper.candidates(r, bars)
    assert c and c[0]["side"] == "BUY"


def test_case_library_scores_roles_with_recency():
    cases = cl.load_csv()
    assert len(cases) >= 10
    st = cl.score(cases, today=dt.date(2026, 11, 23))
    assert st["TP1"]["n"] >= 3 and 0 < st["TP1"]["recent_rate"] <= 1
    old = [{"date": dt.date(2016, 1, 1), "symbol": "X", "role": "r", "outcome": "broke"}] * 5
    new = [{"date": dt.date(2026, 11, 1), "symbol": "X", "role": "r", "outcome": "held"}] * 2
    s = cl.score(old + new, today=dt.date(2026, 11, 23))["r"]
    assert s["rate"] < 0.5 < s["recent_rate"]            # recent holds outweigh old breaks
    assert cl.best_role(st, ["TP1", "nope"])["role"] == "TP1"


def test_rto_fires_on_return_to_origin():
    bars = [_b(1.100, 1.101, 1.096, 1.097), _b(1.097, 1.0975, 1.0950, 1.0955),   # sweep to 1.0950
            _b(1.0955, 1.0990, 1.0954, 1.0985),                                    # turn: origin 1.0955
            _b(1.0985, 1.1000, 1.0980, 1.0995),                                    # displaced +45 ≥ 5
            _b(1.0995, 1.0996, 1.0953, 1.0960)]                                    # back to origin, holds
    r = sniper.rto(bars, "BUY", (1.0945, 1.0960))
    assert r["fired"] and r["level"] == 1.0955 and r["extreme"] == 1.0950


def test_ob_line_buy_above_d1_ob():
    d = [_b(1.10, 1.102, 1.098, 1.101)] * 6 + [_b(1.101, 1.103, 1.095, 1.096),   # bearish OB (high 1.103)
                                               _b(1.096, 1.106, 1.095, 1.105),   # break of the prior highs
                                               _b(1.105, 1.106, 1.100, 1.102),
                                               _b(1.102, 1.104, 1.101, 1.1035)]  # close back above 1.103
    r = sniper.ob_line(d, "BUY")
    assert r["level"] == 1.103 and r["fired"]                  # prev 1.102 ≤ 1.103 < last 1.1035
    d[-1] = _b(1.102, 1.1032, 1.101, 1.1025)                   # still below the line
    r2 = sniper.ob_line(d, "BUY")
    assert not r2["fired"] and r2["why"] == "buy above 1.103"


def test_triggers_pick_fastest_and_list_confirmations():
    one_h = [_b(1.1, 1.101, 1.099, 1.1)] * 5                                       # nothing on 1h
    four_h = [_b(1.100, 1.101, 1.099, 1.1005), _b(1.1005, 1.1008, 1.0990, 1.0992),
              _b(1.0992, 1.0995, 1.0975, 1.0978), _b(1.0978, 1.0980, 1.0960, 1.0965),
              _b(1.0965, 1.1015, 1.0963, 1.1010)]
    t = sniper.triggers({"1h": one_h, "4h": four_h}, "BUY", (1.0955, 1.0970))
    assert t["primary"]["tf"] == "4h" and t["primary"]["type"] == "cisd"
