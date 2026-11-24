import datetime as dt

from services import council as cn
from services import cycles
from services import trade_gate as tg


def test_event_blackouts(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("start_utc,end_utc,symbols,label\n"
                 "2026-11-30T21:00,2026-12-03T23:00,NAS100;US30;NVDA,Huang testimony\n"
                 "2026-12-09T17:00,2026-12-10T03:00,ALL_USD;XAU/USD,FOMC\n")
    t = dt.datetime(2026, 12, 2, 15, tzinfo=dt.timezone.utc)
    assert "Huang testimony" in tg.blackout("NAS100", t, str(f))
    assert tg.blackout("EUR/USD", t, str(f)) is None
    fomc = dt.datetime(2026, 12, 9, 19, tzinfo=dt.timezone.utc)
    assert "FOMC" in tg.blackout("EUR/USD", fomc, str(f)) and "FOMC" in tg.blackout("XAU/USD", fomc, str(f))
    assert tg.blackout("NAS100", dt.datetime(2026, 12, 5, tzinfo=dt.timezone.utc), str(f)) is None


def test_seasonal_agent_midterm_window():
    assert cn.midterm_window(dt.date(2026, 11, 24)) and cn.midterm_window(dt.date(2027, 6, 30))
    assert not cn.midterm_window(dt.date(2027, 7, 1)) and not cn.midterm_window(dt.date(2026, 9, 30))
    d = dt.date(2026, 11, 24)
    assert cn.vote_seasonal("NAS100", "BUY", d)["vote"] == 1 and cn.vote_seasonal("BTC/USD", "BUY", d)["vote"] == 1
    assert cn.vote_seasonal("NAS100", "SELL", d)["vote"] == 0              # never votes against
    assert cn.vote_seasonal("EUR/USD", "BUY", d)["vote"] == 0
    assert cn.vote_seasonal("NAS100", "BUY", dt.date(2027, 8, 1))["vote"] == 0


def test_midterm_windows_on_own_data():
    bars, d, p = [], dt.date(2009, 1, 2), 100.0
    while d <= dt.date(2026, 11, 20):
        if d.weekday() < 5:
            in_win = (d.month >= 10 and d.year % 4 == 2) or (d.month <= 6 and (d.year - 1) % 4 == 2)
            p *= 1.001 if in_win else 0.9998
            bars.append((d, p, p, p, p))
        d += dt.timedelta(days=1)
    m = cycles.midterm_windows(bars, dt.date(2026, 11, 24))
    assert [w["midterm"] for w in m["windows"]] == [2010, 2014, 2018, 2022, 2026]
    assert m["completed"] == 4 and m["pct_positive"] == 100 and m["avg_return_pct"] > 15
    assert m["current"]["midterm"] == 2026 and m["current"]["return_pct"] > 0
    assert "midterm window Oct→Jun: 100% up" in cycles.card_line(cycles.read(bars, dt.date(2026, 11, 24)))
