from routes import ops_check as oc


def test_judges():
    assert oc._judge_calendar({"fetched": 40, "high_upcoming": 6})[0] == "✅"
    assert oc._judge_calendar({"fetched": 0})[0] == "❌"
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
