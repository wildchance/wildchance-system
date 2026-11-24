import datetime as dt

from services import data_audit as da


def test_strikes_audit_flags(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("symbol,expiry,put25,call25,put10,call10,source\n"
                 "EURUSD,2026-12-08,1.0824,1.1006,1.0724,1.1091,owner screenshot 2026-11-24 (2W ATM 1.0917)\n"
                 "GBPJPY,2026-12-08,206.21,205.0,203.30,211.87,owner screenshot 2026-11-24 (ATM 208.45)\n"
                 "AUDUSD,2026-11-25,0.6610,0.6659,0.6582,0.6678,owner screenshot 2026-11-24 (O/N ATM 0.6636)\n"
                 "EURJPY,2026-12-24,172.0,177.5,168.3,179.4,owner screenshot 2026-11-01 (ATM 174.9; interpolated)\n")
    rep = {r["symbol"]: r for r in da.audit_strikes(dt.date(2026, 11, 28), {"EURUSD": 1.0921}, str(p))}
    assert rep["EURUSD"]["status"] == "⚠️" and any("next week" in n for n in rep["EURUSD"]["notes"])
    assert rep["GBPJPY"]["status"] == "❌"                                   # call25 below the pin
    assert "every expiry has passed" in rep["AUDUSD"]["problems"][0]
    assert any("days old" in x for x in rep["EURJPY"]["problems"]) and rep["EURJPY"]["notes"]


def test_message_lists_only_problems():
    m = da.message({"as_of": "2026-11-28", "summary": "✅ 1 · ⚠️ 1 · ❌ 0",
                    "items": [{"source": "calendar", "status": "✅", "problems": []},
                              {"source": "CME bulletin", "symbol": "XAUUSD", "status": "⚠️",
                               "problems": ["bulletin 5 days old"]}]})
    assert "CME bulletin XAUUSD: bulletin 5 days old" in m and "calendar" not in m


def test_retail_age(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("date,symbol,short_avg,short_pips,long_avg,long_pips,price\n2026-11-10,EURUSD,1,1,1,1,1\n")
    assert da.audit_retail(dt.date(2026, 11, 28), str(p))["status"] == "⚠️"


def test_distance_scales_with_vol():
    r_gold = {"expiry": "2027-05-24", "source": "owner screenshot 2026-11-24 (6M ATM 3764.2 IV 24.93%)"}
    r_fx = {"expiry": "2027-05-24", "source": "owner screenshot 2026-11-24 (6M ATM 1.1005 IV 6.60%)"}
    d = dt.date(2026, 11, 24)
    assert da.max_distance(r_gold, d) > 0.4 and 0.13 < da.max_distance(r_fx, d) < 0.15
