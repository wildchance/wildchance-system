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


def test_manual_box_overrides(tmp_path, monkeypatch):
    f = tmp_path / "b.csv"
    f.write_text("symbol,year,low,high,source\nEURUSD,2025,1.02375,1.09389,tv-oanda\nEUR/USD,2024,,,\n")
    monkeypatch.setattr(yc, "BOXES_CSV", str(f))
    yc._manual.update(mtime=None, rows={})
    b = yc.box_for("EUR/USD", [], 2025)
    assert b["source"] == "tv-oanda" and b["high"] == 1.09389
    assert yc.box_for("EUR/USD", [], 2024) is None          # blank row → computed (none here)
    r = yc.analyse("EUR/USD", EU, today=D(2026, 11, 23), years_back=3)
    assert next(y for y in r["years"] if y["year"] == 2025)["source"] == "tv-oanda"


def test_magnet_mean_counts_weekly_crosses():
    box = {"year": 2020, "low": 1.0, "high": 1.2, "range": 0.2}
    bars, d, i = [], D(2020, 4, 6), 0
    while d < D(2021, 4, 1):                    # alternate above/below 1.10 every week
        c = 1.12 if (i // 5) % 2 else 1.08
        if d.weekday() < 5:
            bars.append((d, c, c, c, c)); i += 1
        d += dt.timedelta(days=1)
    assert yc.mid_crosses(box, bars) >= 40


def test_index_tolerance_scales_with_price():
    assert yc.tolerance("NAS100", 25000) == 75.0
    assert yc.tolerance("EUR/USD", 1.1) == 0.003


def test_event_boxes_join_clusters(tmp_path, monkeypatch):
    f = tmp_path / "e.csv"
    f.write_text("symbol,date,label,low,high,source\n"
                 "XAU/USD,2026-09-16,FOMC hike,4252.927,4359.772,m\n"
                 "XAU/USD,2026-10-28,FOMC hold,4033.198,4075.173,m\n"
                 "XAU/USD,2025-01-29,FOMC old,2700,2760,m\n")
    monkeypatch.setattr(yc, "EVENTS_CSV", str(f))
    ev = yc.event_boxes("XAUUSD", D(2026, 11, 2))
    assert [e["year"] for e in ev] == ["FOMC hike Sep-16", "FOMC hold Oct-28"]   # old one aged out
    cl = yc.clusters(yc.all_levels(ev), 15.0)
    # owner's chart: both boxes' −3.5 sit together (3,878.97 / 3,886.29) — the late-Oct low
    assert any({"FOMC hike Sep-16 -3.5", "FOMC hold Oct-28 -3.5"} <= set(c["members"]) for c in cl)
    assert abs(yc.level(ev[1], 2.5) - 4138.13) < 0.05                          # the 4,137.97 high
    assert yc.ladder_name(2.5) == "TP1" and yc.ladder_name(-3) == "TP4 · extreme 3"


def test_ipda_lookbacks():
    lv = yc.ipda_levels(EU, months=2)
    labels = [x["label"] for x in lv]
    assert any(l.startswith("now 20d") for l in labels) and any("60d high" in l for l in labels)
    assert len({x["price"] for x in lv}) == len(lv)                          # deduped
    last60 = EU[-60:]
    assert any(x["price"] == max(b[2] for b in last60) for x in lv)
    r = yc.analyse("EUR/USD", EU, today=D(2026, 11, 23), years_back=3)
    assert r["ipda"] and all("ipda" in c for c in r["clusters"])
