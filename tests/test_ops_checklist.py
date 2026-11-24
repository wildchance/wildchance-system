from routes import ops_check as oc


def test_judges():
    assert oc._judge_calendar({"stored": 74, "last_ok_h": 2.0, "high_upcoming": 6})[0] == "✅"
    assert oc._judge_calendar({"stored": 74, "last_ok_h": None, "backing_off_min": 120})[0] == "⚠️"
    assert oc._judge_calendar({"stored": 0})[0] == "❌"
    assert oc._judge_journal({"trades": 0})[0] == "⚠️"
    assert oc._judge_bridge({"accounts": [{"login": 1}]})[0] == "✅"
    assert oc._judge_snapshot({"prices": {"A": {"price": 1}, "B": {"price": 2}}, "vix": 20})[0] == "✅"
    assert oc._judge_ok("ok", "fine")({"ok": False, "reason": "no data"}) == ("❌", "no data")


def test_trim():
    assert oc._trim({"a": 1}) == {"a": 1}
    assert oc._trim({"a": "x" * 2000}).endswith("…")


def test_checklist_is_private():
    from services.access import is_public
    assert not is_public("/ops/checklist")


def test_checklist_answers_fast_and_resumes(monkeypatch):
    import asyncio
    import time as _t
    oc._done.clear(); oc._running.clear()
    monkeypatch.setattr(oc, "WAIT_S", 0.2)

    async def slow(name, path, judge):
        await asyncio.sleep(0.5 if "snapshot" in path else 0)
        res = {"check": name, "status": "✅", "note": "ok", "path": path}
        oc._done[path] = (_t.time(), res)
        return res
    monkeypatch.setattr(oc, "_run", slow)

    async def go():
        t0 = _t.time()
        r1 = await oc.checklist()
        assert _t.time() - t0 < 0.4
        assert any(c["status"] == "⏳" for c in r1["checks"]) and "still running" in r1["summary"]
        await asyncio.sleep(0.5)
        r2 = await oc.checklist()
        assert all(c["status"] == "✅" for c in r2["checks"])
    asyncio.new_event_loop().run_until_complete(go())
