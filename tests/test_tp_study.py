import datetime as dt
import math
import random

from services import tp_study as ts


def _walk(n=900, vol=0.01, seed=7):
    random.seed(seed)
    bars, p, d = [], 100.0, dt.date(2020, 1, 1)
    for _ in range(n):
        r = random.gauss(0, vol)
        o, c = p, p * math.exp(r)
        h, l = max(o, c) * (1 + abs(random.gauss(0, vol / 3))), min(o, c) * (1 - abs(random.gauss(0, vol / 3)))
        bars.append((d, o, h, l, c))
        p, d = c, d + dt.timedelta(days=1)
    return bars


def test_study_hit_rates_fall_with_distance_and_pick_sweet_spot():
    r = ts.excursion_study(_walk(), horizon=5)
    hits = [t["hit_pct"] for t in r["table"]]
    assert r["samples"] > 1000 and hits == sorted(hits, reverse=True)      # farther = rarer
    assert 1.0 <= r["best_k"] <= 2.0
    assert abs(ts.sigma_daily([b[4] for b in _walk(vol=0.01)]) - 0.01) < 0.004


def test_ladder_1r_2r_and_sweet_spot_tp3():
    lad = ts.ladder("BUY", 100.0, 99.0, unit=2.5, best_k=1.5,
                    structure=[{"price": 103.6, "level": "2025 1"}], cap_price=110.0)
    t = {x["name"]: x for x in lad["targets"]}
    assert t["TP1"]["price"] == 101.0 and t["TP2"]["price"] == 102.0
    assert t["TP3"]["price"] == 103.6 and t["TP3"]["level"].startswith("sweet spot")   # structure in 1σ–2σ
    assert lad["sweet_zone"] == [102.5, 105.0]


def test_ladder_respects_cap_and_volatility():
    capped = ts.ladder("BUY", 100.0, 99.0, unit=2.5, best_k=1.5, cap_price=101.5)
    assert [x["name"] for x in capped["targets"]] == ["TP1"]
    wide = ts.ladder("SELL", 100.0, 104.0, unit=2.5, best_k=1.5)
    assert wide["targets"] == [] and wide["hold"].startswith("stop wider than the 1σ")
    sell = ts.ladder("SELL", 100.0, 101.0, unit=2.5, best_k=1.75)
    assert [round(x["price"], 3) for x in sell["targets"]] == [99.0, 98.0, 95.625]
