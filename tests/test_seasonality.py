import datetime as dt

from seasonality.engine import seasonal_bias, monthly_returns_by_month
from edgefinder.engine import _seasonality_factor, score_pair


def _series(monthly_pct, start=(2018, 1), base=100.0):
    """Build (date, close) monthly bars from a list of month % returns."""
    y, m = start
    price = base
    out = [(dt.date(y, m, 1), price)]
    for p in monthly_pct:
        m += 1
        if m > 12:
            m = 1
            y += 1
        price *= (1 + p)
        out.append((dt.date(y, m, 1), price))
    return out


def test_monthly_bucketing():
    bars = _series([0.01, -0.02, 0.03])   # Feb+1%, Mar-2%, Apr+3%
    by = monthly_returns_by_month(bars)
    assert round(by[2][0], 4) == 0.01 and round(by[3][0], 4) == -0.02


def test_seasonal_bias_strong_long():
    # every April (month 4) strongly positive across years
    bars = _series([0.0, 0.0, 0.06,  # 2018 Apr +6%
                    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 0.07,  # 2019 Apr +7%
                    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 0.05], start=(2018, 1))  # 2020 Apr +5%
    s = seasonal_bias(bars, 4)
    assert s["dir"] == 1 and s["strength"] == "strong" and s["samples"] >= 2


def test_seasonal_insufficient_history_is_neutral():
    s = seasonal_bias(_series([0.01]), 7)
    assert s["dir"] == 0 and s["strength"] == "none"


def test_seasonality_factor_scoring():
    assert _seasonality_factor({"dir": 1, "strength": "strong", "note": "x"})["score"] == 2
    assert _seasonality_factor({"dir": -1, "strength": "mild", "note": "x"})["score"] == -1
    assert _seasonality_factor({"dir": 0, "strength": "none"})["score"] == 0
    assert _seasonality_factor(None)["score"] == 0


def test_score_pair_includes_seasonality_when_provided():
    sig = {"pair": "EUR/USD", "retail_long": 68, "retail_extreme": 68,
           "contrarian": "short", "cot_agrees": True, "cot_net": -30000,
           "cot_rank": 0.3, "verdict": "SHORT", "confidence": 70}   # retail-2, cot-1
    row = score_pair(sig, seasonal={"dir": -1, "strength": "strong", "note": "x"})
    assert any(f["name"] == "seasonality" for f in row["factors"])
    assert row["score"] == -5      # -2 retail, -1 cot, -2 seasonality
