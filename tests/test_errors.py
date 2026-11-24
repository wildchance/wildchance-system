from services import errors


def test_record_summary_and_digest():
    errors._counts.clear(); errors._recent.clear()
    errors.record("cron_fail", "GET /sniper/scan", "HTTP 500")
    errors.record("cron_fail", "GET /sniper/scan", "HTTP 500")
    try:
        raise ValueError("bad bar")
    except ValueError as e:
        errors.swallow("regime.compute EUR/USD", e)
    s = errors.summary()
    assert s["total"] == 3 and s["by_kind"] == {"cron_fail": 2, "swallowed": 1}
    assert s["top"][0] == {"kind": "cron_fail", "where": "GET /sniper/scan", "n": 2}
    assert "ValueError: bad bar" in errors.recent(1)[0]["detail"]
    t = errors.digest_text("2026-11-24", errors._counts[s["day"]], ["forexfactory_calendar"])
    assert "cron_fail × 2" in t and "forexfactory_calendar" in t
    assert "👍" in errors.digest_text("2026-11-24", {}, [])


def test_middleware_counts_500(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    errors._counts.clear()

    @main.app.get("/__boom_test")
    async def boom():
        raise RuntimeError("x")
    c = TestClient(main.app, raise_server_exceptions=False)
    c.get("/__boom_test", headers={"x-internal-token": __import__("services.access", fromlist=["x"]).INTERNAL_TOKEN})
    assert errors.summary()["by_kind"].get("http_500") == 1


def test_fast_lane_split():
    from routes import cron
    from services.cron_schedule import jobs
    names = [j.name for j in jobs() if cron._is_fast(j)]
    assert len(names) == 12 and all(n.startswith("signal_track_") for n in names)
