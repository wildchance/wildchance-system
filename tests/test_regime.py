from services import regime as rg


def test_efficiency_and_classify():
    up = [1 + 0.01 * i for i in range(30)]
    chop = [1 + (0.01 if i % 2 else -0.01) for i in range(30)]
    assert rg.efficiency(up) == 1.0 and abs(rg.efficiency(chop)) < 0.1
    assert rg.classify(0.6, 50, 18, False)["regime"] == "TRENDING"
    assert rg.classify(0.6, 50, 18, False)["trend"] == "UP"
    assert rg.classify(0.1, 50, 18, False)["regime"] == "RANGING"
    assert rg.classify(0.1, 95, 18, False)["regime"] == "STRESSED"
    assert rg.classify(0.1, 50, 18, True)["regime"] == "EVENT"


def test_routing_rules():
    trend_up = rg.classify(0.6, 50, 18, False)
    rng = rg.classify(0.1, 50, 18, False)
    assert "not fading" in rg.route(trend_up, "reversion", "SELL")
    assert rg.route(trend_up, "reversion", "BUY") is None             # buying the dip in an up-trend is fine
    assert "ranges" in rg.route(rng, "continuation", "BUY")
    assert rg.route(rng, "reversion", "SELL") is None and rg.route(trend_up, None, "SELL") is None


def test_risk_budget_shrinks_never_grows():
    calm = rg.budget(rg.classify(0.1, 30, 15, False), 0, False)
    assert calm["risk_pct"] == 0.5 and calm["why"] == []
    hot = rg.budget(rg.classify(0.1, 85, 27, False), 3, True)
    assert hot["risk_pct"] == round(max(0.15, 0.5 * 0.6 * 0.75 * 0.7 * 0.75), 2)
    worst = rg.budget(rg.classify(0.1, 95, 40, True), 5, True)
    assert worst["risk_pct"] == 0.15


def test_card_lines_use_cached_budget(monkeypatch):
    import time
    from propfirm.target_lots import card_lines
    monkeypatch.setitem(rg._budget, "EURUSD", (time.time(), {"risk_pct": 0.25}))
    lines = card_lines("EUR/USD", 1.0900, 1.0880, [{"name": "TP1", "price": 1.0940}], balances=[5000])
    assert "risk cap 0.25%" in lines[0]


def test_unknown_when_no_data():
    assert rg.classify(None, None, 18, False)["regime"] == "UNKNOWN"
    assert rg.route(rg.classify(None, None, 18, False), "continuation", "BUY") is None
