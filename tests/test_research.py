import datetime as dt

from services import backtest as bt
from services import council as cn
from services.snapshots import realised_r


def test_realised_r_equal_split():
    s = {"result": "win", "entry": 1.0, "stop": 0.99, "targets": [1.01, 1.02], "hits": [{"tp": 1}], "filled": True}
    assert realised_r(s) == 0.5                        # TP1 (1R) half, rest at entry
    s["hits"] = [{"tp": 1}, {"tp": 2}]
    assert realised_r(s) == 1.5
    assert realised_r({**s, "result": "loss", "hits": []}) == -1.0


def test_stats_and_walk_forward():
    trades = [{"r": r} for r in [1, -1, 0.5, 1, -1, 1.5, 0.5, -1, 1, 1, -1, 0.5]]
    s = bt.stats(trades, years=1)
    assert s["trades"] == 12 and s["max_drawdown_r"] == 1.0 and s["profit_factor"] == 1.75
    wf = bt.walk_forward(trades)
    assert len(wf["folds"]) == 4 and wf["positive_folds"] >= 3 and wf["stable"]
    assert bt.walk_forward(trades[:5])["stable"] is None


def test_z_backtest_on_synthetic_mean_reversion():
    import math
    bars, t0 = [], dt.datetime(2026, 1, 5, 0, 0)
    for i in range(600):
        c = 1.10 + 0.002 * math.sin(i / 6) + (0.004 if i % 97 == 50 else 0)    # oscillation + spikes
        t = t0 + dt.timedelta(hours=i)
        bars.append({"time": t.isoformat(sep=" "), "open": c, "high": c + 0.0004, "low": c - 0.0004, "close": c})
    trades = bt.z_trades("EUR/USD", bars, {})
    assert trades and all(t["side"] in ("BUY", "SELL") for t in trades)
    assert bt.stats(trades)["trades"] == len(trades)


def test_weighted_council_probability():
    votes = [{"agent": "cot", "vote": 1}, {"agent": "vix", "vote": 1}, {"agent": "news", "vote": -1},
             {"agent": "macro", "vote": 0}]
    strong = {"cot": {"accuracy": 0.75, "n": 30}, "vix": {"accuracy": 0.7, "n": 30}, "news": {"accuracy": 0.5, "n": 30}}
    weak = {"cot": {"accuracy": 0.45, "n": 30}, "vix": {"accuracy": 0.5, "n": 30}, "news": {"accuracy": 0.8, "n": 30}}
    assert cn.probability(votes, strong)["prob"] > 0.75
    assert cn.probability(votes, weak)["prob"] < 0.4
    assert 0.5 < cn.probability(votes, None)["prob"] < 0.6          # unscored agents count weakly
    line = cn.summary({"verdict": "GO", "for": 4, "against": 0, "votes": [], "prob": 0.66, "prob_enforced": False})
    assert "P(TP1) 66% (learning)" in line
