import asyncio
import datetime as dt

from services import calendar_paste as cp, free_macro_feeds as fmf, macro_surprise as ms


def test_lookalike_titles_not_misread():
    for t in ("ISM Manufacturing Prices", "Advance GDP Price Index q/q", "Revised UoM Inflation Expectations",
              "MPC Official Bank Rate Votes", "Prelim Benchmark Payrolls Revision", "Employment Cost Index q/q",
              "RBA Rate Statement"):
        assert ms.classify(t) is None, t
    assert ms.classify("Ivey PMI") == "mpmi" and ms.classify("Claimant Count Change") == "claims"


def test_seed_history_loads_and_scores():
    ev = ms.seed_events()
    assert len(ev) >= 190 and ev[0]["when"] == "2026-07-23T12:30"          # 15:30 UTC+3 → 12:30 UTC
    st, n = ms.apply({}, sorted(ev, key=lambda e: e["when"]))
    assert n == len(ev)
    assert st["USD"]["gdp"]["score"] == 2 and st["USD"]["gdp"]["actual"] == "4.3%"      # 4.3 vs 3.5
    assert st["NZD"]["rate"]["score"] == -2                                  # held at 2.75 vs 3.00 expected
    assert st["GBP"]["rate"]["score"] == 1 and st["JPY"]["rate"]["score"] == 0
    assert st["AUD"]["unemp"]["score"] == -2                                 # 4.9 vs 4.7
    assert ms.category_scores(st, "AUD")["jobs"] == -4
    assert ms.surprise_index(st, "USD", now=dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc)) is not None


def test_policy_rates_from_owner_calendar():
    assert fmf.POLICY_RATES["RBA"] == 4.85 and fmf.POLICY_RATES["FED"] == 4.00 and fmf.POLICY_RATES["ECB"] == 2.65


SAMPLE = ("Mon\nJul 20\n3:30pm\nCAD\t\t\nCPI m/m\n\t-0.4%\t-0.2%\t1.0%\t\nTue\nJul 21\n9:00am\nGBP\t\t\n"
          "Claimant Count Change\n\t6.7K\t29.4K\t\n1.3K\n\nGBP\t\t\nAverage Earnings Index 3m/y\n\t4.3%\t4.5%\t4.4%\t\n"
          "Fri\nNov 27\n2:30am\nJPY\t\t\nTokyo Core CPI y/y\n\t\t3.0%\t2.6%\t\n")


def test_tab_layout_paste():
    ev = cp.parse(SAMPLE, today=dt.date(2026, 11, 26))
    got = {e["title"]: (e["actual"], e["forecast"], e["previous"]) for e in ev}
    assert got["CPI m/m"] == ("-0.4%", "-0.2%", "1.0%")
    assert got["Claimant Count Change"] == ("6.7K", "29.4K", "1.3K")              # revised previous on its own line
    assert got["Tokyo Core CPI y/y"] == ("", "3.0%", "2.6%")                      # not printed yet
    assert [e["time_utc"][:16] for e in ev][:2] == ["2026-07-20T12:30", "2026-07-21T06:00"]


def test_paste_rows_reach_macro_even_if_old(monkeypatch):
    from services import data_store
    store = {}

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    monkeypatch.setattr(ms, "seed_events", lambda path=None: [])
    monkeypatch.setattr(ms, "_events", lambda: [])
    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(ms.ingest(extra=cp.parse(SAMPLE, today=dt.date(2026, 11, 26))))
    finally:
        loop.close()
    assert r["new_prints"] == 2 and store[ms.KEY]["CAD"]["cpi"]["score"] == -2 and store[ms.KEY]["GBP"]["claims"]["score"] == 2
