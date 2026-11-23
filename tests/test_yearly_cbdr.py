import datetime as dt

from gold.weekly import weekly_bias
from services import yearly_cbdr as yc


def _path(points):
    """Daily weekday bars along straight lines between (date, close) points."""
    bars, prev = [], points[0][1]
    for (d0, p0), (d1, p1) in zip(points, points[1:]):
        n = (d1 - d0).days
        for i in range(n):
            d = d0 + dt.timedelta(days=i)
            if d.weekday() >= 5:
                continue
            c = p0 + (p1 - p0) * i / n
            bars.append((d, prev, max(prev, c) + 0.0005, min(prev, c) - 0.0005, c))
            prev = c
    return bars


D = dt.date
# EUR/USD-shaped: 2025 Q1 body 1.02375 → 1.09389, peak ~2.6 (1.2082), 2026 Q1 1.14138 → 1.20482,
# then down to 1.0984 (Nov 2026)
EU = _path([(D(2024, 1, 1), 1.09), (D(2024, 12, 31), 1.04), (D(2025, 1, 10), 1.02375),
            (D(2025, 3, 20), 1.09389), (D(2025, 3, 31), 1.08), (D(2025, 9, 15), 1.17),
            (D(2026, 1, 2), 1.15), (D(2026, 1, 20), 1.14138), (D(2026, 2, 10), 1.2082),
            (D(2026, 2, 12), 1.20482), (D(2026, 3, 31), 1.16), (D(2026, 11, 23), 1.0984)])


def test_q1_box_uses_bodies():
    b = yc.q1_box(EU, 2025)
    assert abs(b["low"] - 1.02375) < 1e-4 and abs(b["high"] - 1.09389) < 2e-3
    # 2.5 extension ≈ the user's 1.19909
    assert abs(yc.level(b, 2.5) - 1.19909) < 0.004


def test_analyse_eurusd_shape():
    r = yc.analyse("EUR/USD", EU, today=D(2026, 11, 23), years_back=3)
    assert r["ok"] and [y["year"] for y in r["years"]] == [2024, 2025, 2026]
    y25 = next(y for y in r["years"] if y["year"] == 2025)
    assert y25["peak"]["up"] == 2.5                    # reached 2.5, failed to break 3
    assert r["control"] == 2026
    assert r["ote"]["leg"] == "bull" and r["ote"]["side"] == "BUY"
    p = r["plan"]
    assert p["side"] == "BUY"
    if p["status"] == "limit":
        assert p["entry"] < r["price"] and p["stop"] < p["entry"]
        assert all(t["price"] > p["entry"] for t in p["targets"])
    assert "Yearly CBDR" in yc.card(r)


def test_current_year_box_waits_for_q1_close():
    r = yc.analyse("EUR/USD", [b for b in EU if b[0] <= D(2026, 2, 20)], today=D(2026, 2, 20),
                   years_back=2)
    assert [y["year"] for y in r["years"]] == [2024, 2025] and r["forming"] == 2026


def test_clusters_need_two_years():
    lv = [{"year": 2025, "dev": "1", "price": 1.0939}, {"year": 2026, "dev": "-0.5", "price": 1.1097},
          {"year": 2024, "dev": "2", "price": 1.0950}]
    cl = yc.clusters(sorted(lv, key=lambda x: x["price"]), 0.003)
    assert len(cl) == 1 and cl[0]["years"] == [2024, 2025]


def test_weekly_prior_fallback():
    # last week: swept Monday low then reclaimed → long; this week inside Monday range
    bars = [(D(2026, 11, 16), 1.10, 1.105, 1.095, 1.10), (D(2026, 11, 17), 1.10, 1.103, 1.090, 1.101),
            (D(2026, 11, 20), 1.10, 1.104, 1.098, 1.102),
            (D(2026, 11, 23), 1.10, 1.110, 1.090, 1.10)]
    assert weekly_bias(bars)["bias"] == "neutral"
    r = weekly_bias(bars, prior_fallback=True)
    assert r["bias"] == "long" and r["profile"].startswith("prior_week_")
    assert "last week" in r["reason"]
