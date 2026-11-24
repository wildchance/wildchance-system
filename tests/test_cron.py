"""In-app scheduler: DST-aware slots, de-dup, catch-up, /cron/tick auth + execution."""

import datetime as dt

from services import cron_schedule as cs

U = dt.timezone.utc


def _job(name):
    return [j for j in cs.jobs() if j.name == name][0]


def test_ny_job_follows_dst():
    j = _job("ny_open_0900")
    winter = cs.slots(j, dt.datetime(2026, 11, 23, 14, 5, tzinfo=U), 10)   # EST: 14:00 UTC
    summer = cs.slots(j, dt.datetime(2026, 7, 15, 13, 5, tzinfo=U), 10)    # EDT: 13:00 UTC
    assert winter == [dt.datetime(2026, 11, 23, 14, 0, tzinfo=U)]
    assert summer == [dt.datetime(2026, 7, 15, 13, 0, tzinfo=U)]
    assert cs.slots(j, dt.datetime(2026, 11, 23, 13, 5, tzinfo=U), 10) == []  # wrong hour in winter
    assert cs.slots(j, dt.datetime(2026, 11, 22, 14, 5, tzinfo=U), 10) == []  # Sunday


def test_london_and_gap_weeks():
    j = _job("london_open_0800")
    # 2026-03-10: US already on EDT, UK still GMT → London 08:00 = 08:00 UTC
    assert cs.slots(j, dt.datetime(2026, 3, 10, 8, 2, tzinfo=U), 10)
    assert cs.slots(_job("prelondon_0300ny"), dt.datetime(2026, 3, 10, 7, 2, tzinfo=U), 10)


def test_utc_filters():
    assert cs.slots(_job("monthly_scorecard"), dt.datetime(2026, 12, 1, 8, 1, tzinfo=U), 10)
    assert not cs.slots(_job("monthly_scorecard"), dt.datetime(2026, 12, 2, 8, 1, tzinfo=U), 10)
    assert cs.slots(_job("weekly_cot_fri"), dt.datetime(2026, 11, 27, 21, 1, tzinfo=U), 10)
    assert not cs.slots(_job("weekly_cot_fri"), dt.datetime(2026, 11, 26, 21, 1, tzinfo=U), 10)
    assert len(cs.slots(_job("hourly"), dt.datetime(2026, 11, 23, 10, 30, tzinfo=U), 70)) == 1
    assert len(cs.slots(_job("hourly"), dt.datetime(2026, 11, 23, 10, 30, tzinfo=U), 100)) == 2


def test_due_dedups_and_catches_up():
    now = dt.datetime(2026, 11, 23, 9, 1, tzinfo=U)               # 09:00 UTC digest + hourly
    keys = [k for k, _ in cs.due(now, lookback_min=5)]
    assert "digest_ny@2026-11-23T09:00Z" in keys and "hourly@2026-11-23T09:00Z" in keys
    assert cs.due(now, done=keys, lookback_min=5) == []
    late = dt.datetime(2026, 11, 23, 9, 50, tzinfo=U)               # missed tick → caught up
    assert "digest_ny@2026-11-23T09:00Z" in [k for k, _ in cs.due(late)]


def test_every_workflow_call_is_ported():
    paths = {p for j in cs.jobs() for _, p in j.calls}
    for must in ("/cbdr/limits/XAU/USD?window=prelondon&notify=true", "/candlerange/friday",
                 "/gold/alerter/scan?notify=true", "/sentiment/scan", "/swing/scan?notify=true",
                 "/benner/alert", "/quarterly/alert/NAS100", "/gold/stratops/fit"):
        assert must in paths
    assert len(cs.jobs()) == 50
    assert cs.redact("/execution/reconcile?token=abc&x=1") == "/execution/reconcile?token=***&x=1"


def test_tick_endpoint(monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    import routes.cron as rc
    from main import app
    monkeypatch.setattr(rc, "TOKEN", "ct")
    called = []

    async def fake_calls(calls, now=None):
        called.extend(calls)
        return [{"call": f"{m} {p}", "code": 502, "detail": "no data"} for m, p in calls]

    async def yes(slot):
        return True
    alerts = []

    async def fake_alert(key, text, ttl_hours=None):
        alerts.append(key)
        return True
    import services.realtime as rt
    monkeypatch.setattr(rc, "_call_all", fake_calls)
    monkeypatch.setattr(rc, "_claim", yes)
    monkeypatch.setattr(rt, "alert_once", fake_alert)
    monkeypatch.setattr(rc.cs, "due", lambda now: [("hourly@x", _job("hourly"))])
    c = TestClient(app)
    assert c.get("/cron/tick").status_code == 403
    r = c.get("/cron/tick", headers={"X-Cron-Token": "ct"}).json()
    assert r["accepted"] and r["due"] == ["hourly@x"]
    assert len(called) == len(_job("hourly").calls)                 # ran in background
    assert alerts == ["cron-outage"]                                # ≥60% failed → loud
    st = c.get("/cron/status").json()
    assert st["ticks"][0]["jobs"][0]["slot"] == "hourly@x"
    assert c.get("/cron/run?job=nope&token=ct").status_code == 404
    jobs = c.get("/cron/jobs").json()["jobs"]
    assert len(jobs) == 50 and all(j["next_utc"] for j in jobs)


# --- data freshness -------------------------------------------------------------------
def test_freshness_grades():
    from services import freshness as f
    wed = dt.datetime(2026, 11, 25, 15, 0, tzinfo=U)
    sat = dt.datetime(2026, 11, 28, 15, 0, tzinfo=U)
    sun_open = dt.datetime(2026, 11, 29, 23, 0, tzinfo=U)      # 18:00 NY → open
    fri_close = dt.datetime(2026, 11, 27, 22, 30, tzinfo=U)    # 17:30 NY → closed
    assert f.market_open(wed) and f.market_open(sun_open)
    assert not f.market_open(sat) and not f.market_open(fri_close)
    assert f.grade(30, wed) == "OK" and f.grade(500, wed) == "STALE"
    assert f.grade(5000, sat) == "CLOSED"
    assert f.grade(None, wed, uptime_min=10) == "UNKNOWN"
    assert f.grade(None, wed, uptime_min=200) == "STALE"


def test_health_reports_data_and_bridge_is_header_only(monkeypatch):
    from fastapi.testclient import TestClient
    import routes.bridge as rb
    from main import app
    monkeypatch.setattr(rb, "TOKEN", "tk")
    c = TestClient(app)
    h = c.get("/health").json()
    assert "data" in h and h["data"]["status"] in ("OK", "STALE", "CLOSED", "UNKNOWN")
    body = {"symbol": "XAUUSD", "timeframe": "H1", "bars": []}
    assert c.post("/bridge/bars?token=tk", json=body).status_code == 403     # query ignored
    assert c.post("/bridge/bars", json=body, headers={"X-Bridge-Token": "tk"}).status_code == 200


# --- central trade gate in the scheduler ----------------------------------------------
def test_call_classes():
    c = cs.call_class
    assert c("/gold/scan?balance=5000&notify=true") == "entry"
    assert c("/candlerange/crt?session=ny&execute=true") == "entry"
    assert c("/swing/scan?notify=true") == "entry" and c("/zconfluence/scan?notify=true") == "entry"
    assert c("/alerts/session?label=New%20York") == "digest" and c("/intraday/scan") == "digest"
    assert c("/gold/monitor") == "other" and c("/wildchance/scrape?tier=6h") == "other"
    assert c("/scorecard/weekly") == "other" and c("/sentiment/scan") == "other"


def test_hold_reasons_weekend_and_cbdr():
    sat = dt.datetime(2026, 11, 28, 12, 0, tzinfo=U)
    cbdr = dt.datetime(2026, 11, 23, 20, 0, tzinfo=U)          # Mon 15:00 NY
    london = dt.datetime(2026, 11, 24, 8, 0, tzinfo=U)         # Tue 03:00 NY
    assert cs.hold_reason("/gold/scan?notify=true", sat) == "market closed"
    assert cs.hold_reason("/alerts/session?label=x", sat) == "market closed"
    assert cs.hold_reason("/gold/monitor", sat) is None             # manage always runs
    assert "CBDR" in cs.hold_reason("/gold/scan?notify=true", cbdr)
    assert cs.hold_reason("/alerts/session?label=x", cbdr) is None  # digest OK in CBDR window
    assert cs.hold_reason("/gold/scan?notify=true", london) is None


def test_held_calls_dont_count_as_outage(monkeypatch):
    import asyncio
    import routes.cron as rc
    import services.realtime as rt
    alerts = []

    async def fake_alert(key, text, ttl_hours=None):
        alerts.append(key)
        return True

    async def yes(slot):
        return True
    monkeypatch.setattr(rt, "alert_once", fake_alert)
    monkeypatch.setattr(rc, "_claim", yes)
    sat = dt.datetime(2026, 11, 28, 12, 0, tzinfo=U)
    job = cs.Job("t", cs.UTC, (12,), 0, (("POST", "/gold/scan?notify=true"),
                                          ("POST", "/gold/intraday?notify=true"),
                                          ("POST", "/gold/alerter/scan?notify=true"),
                                          ("GET", "/swing/scan?notify=true")))
    loop = asyncio.new_event_loop()
    try:
        rec = loop.run_until_complete(rc._run_jobs([("t@x", job)], sat))
    finally:
        loop.close()
    res = rec["jobs"][0]["results"]
    assert all(r["code"] == 0 and r["detail"] == "held: market closed" for r in res)
    assert alerts == []                                            # no false outage alarm


def test_no_job_is_dead_on_arrival():
    """Every job with entry calls must have at least one weekday slot where the central
    gate lets entries through (regression: newcbdr at 14:00 NY, Sunday review at 17:00 NY)."""
    for j in cs.jobs():
        entries = [p for _, p in j.calls if cs.call_class(p) == "entry"]
        if not entries:
            continue
        ok = False
        for day in range(23, 30):                                 # one full week (Nov 2026)
            for h in range(24):
                now = dt.datetime(2026, 11, day, h, 30, tzinfo=U)
                for slot in cs.slots(j, now, 60):
                    if cs.hold_reason(entries[0], slot) is None:
                        ok = True
        assert ok, f"{j.name}: entries are held at every scheduled slot"


def test_rescheduled_jobs():
    j = _job("cbdr_complete_2000ny")
    assert cs.slots(j, dt.datetime(2026, 11, 24, 1, 10, tzinfo=U), 10)      # 20:05 NY (EST)
    w = _job("weekly_review_sun")
    assert cs.slots(w, dt.datetime(2026, 11, 30, 1, 10, tzinfo=U), 10)      # Sun 20:05 NY
    assert cs.hold_reason("/gold/scan?notify=true", dt.datetime(2026, 11, 30, 1, 5, tzinfo=U)) is None
