import datetime as dt

from services import econ_calendar as ec

RAW = [{"title": "Core PCE Price Index m/m", "country": "USD", "date": "2026-11-25T08:30:00-05:00",
        "impact": "High", "forecast": "0.3%", "previous": "0.3%"},
       {"title": "CB Consumer Confidence", "country": "USD", "date": "2026-11-24T10:00:00-05:00",
        "impact": "Medium", "forecast": "92.4", "previous": "91.7"},
       {"title": "Bank Holiday", "country": "USD", "date": "2026-11-26T00:00:00-05:00", "impact": "Holiday"},
       {"title": "bad", "country": "USD", "date": "nope"}]
U = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)


def test_parse_to_utc():
    ev = ec.parse(RAW)
    assert len(ev) == 3 and ev[0]["time_utc"] == "2026-11-25T13:30+00:00"


def test_blackout_high_only_and_currency_mapping():
    ev = ec.parse(RAW)
    assert ec.blackout("EUR/USD", U("2026-11-25T13:10"), ev)
    assert ec.blackout("XAU/USD", U("2026-11-25T14:20"), ev)
    assert ec.blackout("NAS100", U("2026-11-25T13:00"), ev)
    assert not ec.blackout("EUR/GBP", U("2026-11-25T13:30"), ev)
    assert not ec.blackout("EUR/USD", U("2026-11-25T14:40"), ev)          # after +60 min
    assert not ec.blackout("EUR/USD", U("2026-11-24T15:00"), ev)          # medium, not learned
    assert not ec.blackout("EUR/USD", U("2026-11-26T00:00"), ev)          # holiday


def test_medium_event_promoted_after_learning_big_moves(monkeypatch):
    ev = ec.parse(RAW)
    cb = ev[0] if ev[0]["title"].startswith("CB") else ev[1]
    monkeypatch.setattr(ec, "_moves", {ec.move_key(cb, "EUR/USD"): {"n": 3, "avg_pips": 31.0, "max_pips": 45}})
    assert ec.blackout("EUR/USD", U("2026-11-24T15:00"), ev)
    assert "promoted" in ec.week_card([{**cb, "typical": ec.typical_move(cb)}])


def test_merge_keeps_actuals_and_drops_old():
    old = ec.parse(RAW)
    old[0]["learned"] = True
    new = ec.parse(RAW[:1])
    m = ec.merge(old, new, now=U("2026-11-25T00:00"))
    pce = [e for e in m if e["title"].startswith("Core PCE")][0]
    assert pce["learned"] is True and len(m) == 3
    assert ec.merge(old, [], now=U("2027-03-01T00:00")) == []


def test_measure_range_in_pips():
    bars = [{"time": "2026-11-25T12:00:00", "high": 1.0920, "low": 1.0910},
            {"time": "2026-11-25T13:00:00", "high": 1.0950, "low": 1.0905},
            {"time": "2026-11-25T14:00:00", "high": 1.0960, "low": 1.0930},
            {"time": "2026-11-25T15:00:00", "high": 1.0999, "low": 1.0900}]
    assert ec.measure(bars, U("2026-11-25T13:30"), 0.0001) == 55.0


def test_currencies():
    assert ec.currencies("GBP/JPY") == {"GBP", "JPY"}
    assert ec.currencies("XAU/USD") == {"USD"} and ec.currencies("NVDA") == {"USD"}


def test_status_uses_persisted_health_after_restart(monkeypatch):
    import time
    from services import data_feeds as df
    monkeypatch.setitem(ec._meta, "last_ok", 0.0)
    monkeypatch.setitem(df._health, "forexfactory_calendar", {"last_ok": time.time() - 2 * 3600})
    assert ec.status()["last_ok_h"] == 2.0
