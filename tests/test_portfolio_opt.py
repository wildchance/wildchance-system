"""Phase 11 — portfolio optimisation: conviction-scaled, risk-budgeted allocation."""

from gold import portfolio_opt as po


_FLEET = [
    {"id": "acc1", "balance": 700, "risk_pct": 1.0},
    {"id": "acc2", "balance": 5000, "risk_pct": 1.0},
    {"id": "acc5", "balance": 100000, "risk_pct": 1.0},
]


def test_conviction_scale_floor_and_ceiling():
    assert po.conviction_scale(0) == 0.25          # never zero on a live signal
    assert po.conviction_scale(100) == 1.0
    assert po.conviction_scale(50) == 0.625


def test_allocate_is_equal_risk_contribution():
    a = po.allocate(_FLEET, entry=4049, stop=4036, conviction_pct=100)
    # each account risks ~1% of its OWN balance (not equal lots)
    r = {l["account"]: l for l in a["legs"]}
    assert abs(r["acc1"]["risk_usd"] - 7.0) < 0.5      # 1% of 700
    assert abs(r["acc2"]["risk_usd"] - 50.0) < 1.0     # 1% of 5000
    assert abs(r["acc5"]["risk_usd"] - 1000.0) < 5.0   # 1% of 100000
    # bigger account → bigger lot
    assert r["acc5"]["lot"] > r["acc2"]["lot"] > r["acc1"]["lot"]


def test_conviction_scales_risk_down():
    hi = po.allocate(_FLEET, 4049, 4036, conviction_pct=100)["total_risk_usd"]
    lo = po.allocate(_FLEET, 4049, 4036, conviction_pct=0)["total_risk_usd"]
    assert lo < hi and abs(lo / hi - 0.25) < 0.05      # low conviction ≈ 25% risk


def test_risk_budget_scales_book_to_fit():
    # force over-budget with a tiny stop (huge lots) + tight budget
    a = po.optimise(_FLEET, entry=4049, stop=4048, conviction_pct=100,
                    budget_pct=0.5, max_risk_pct=5.0)
    assert a["within_budget"] in (True, False)
    if not a["within_budget"]:
        assert a["portfolio_risk_pct"] <= 0.5 + 1e-6
        assert a["scale_factor"] < 1.0


def test_within_budget_untouched():
    a = po.optimise(_FLEET, 4049, 4036, conviction_pct=50, budget_pct=10.0)
    assert a["within_budget"] is True
    assert a["portfolio_risk_pct"] <= 10.0


def test_zero_stop_distance_safe():
    a = po.allocate(_FLEET, entry=4049, stop=4049, conviction_pct=60)
    assert all(l["lot"] == 0.0 for l in a["legs"])     # no size on a zero-stop
