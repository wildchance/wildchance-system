import datetime as dt

from gold.ict import classify_week, confluence, PROFILES
from gold.risk_engine import phase_plan


def _htf(n=20, end=dt.date(2026, 7, 3), base=4350):
    """Prior bars ranging 4300–4400 → HTF equilibrium ≈ 4350."""
    bars = []
    for i in range(n):
        d = end - dt.timedelta(days=n - 1 - i)
        c = base + (30 if i % 2 else -30)
        bars.append((d, c, c + 20, c - 20, c))
    return bars


def _week(rows, start=dt.date(2026, 7, 6)):   # Monday
    return [(start + dt.timedelta(days=i), *r) for i, r in enumerate(rows)]


def test_all_twelve_profiles_defined():
    assert len(PROFILES) == 12 and PROFILES[1][1] == "long" and PROFILES[2][1] == "short"


def test_classic_tuesday_low_is_long():
    daily = _htf() + _week([
        (4350, 4360, 4345, 4352),           # Mon near eq
        (4340, 4345, 4300, 4342),           # Tue: week low 4300 (discount), closes up
    ])
    r = classify_week(daily)
    assert r["bias"] == "long" and r["profile_id"] == 1 and r["zone"] == "discount"


def test_classic_tuesday_high_is_short():
    daily = _htf() + _week([
        (4350, 4358, 4348, 4352),           # Mon
        (4360, 4400, 4356, 4358),           # Tue: week high 4400 (premium), rejects & closes down
    ])
    r = classify_week(daily)
    assert r["bias"] == "short" and r["profile_id"] == 2 and r["zone"] == "premium"


def test_confluence_mapping():
    assert confluence({"bias": "long"}, "buy") == "confirms"
    assert confluence({"bias": "long"}, "sell") == "diverges"
    assert confluence({"bias": "neutral"}, "long") == "neutral"
    assert confluence(None, "long") == "neutral"


def test_insufficient_data():
    assert classify_week([(dt.date(2026, 7, 6), 1, 2, 0.5, 1.5)]) is None


def test_phase_plan_counts_and_targets():
    p = phase_plan(5000)
    assert p["per_trade_usd"] == {"base_60": 60.0, "agg_120": 120.0}
    assert p["phase1_6pct"] == {"target_usd": 300.0, "trades_base": 6, "trades_agg": 3}
    assert p["cumulative_12pct"]["trades_base"] == 12
    assert p["payout_18pct"]["trades_agg"] == 9


def test_phase_plan_scales():
    p = phase_plan(25000)
    assert p["per_trade_usd"] == {"base_60": 300.0, "agg_120": 600.0}   # scales with balance
    assert p["phase1_6pct"]["trades_base"] == 6                          # count constant
