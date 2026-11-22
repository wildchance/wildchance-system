"""Z-score × COT/retail confluence setups (fixtures from the live Telegram screenshots)."""

import datetime as dt

from services import zconfluence as zc


def _breach(sym, z, last, mean, sd, fresh=True):
    lean = "SELL" if z > 0 else "BUY"
    return {"ok": True, "fired": True, "fresh": fresh, "symbol": sym, "z": z, "lean": lean,
            "last_close": last, "mean": mean, "sd": sd}


SIGNALS = [  # setup digest "After open": retail-only conf 94 rows
    {"pair": "EUR/USD", "verdict": "SHORT", "confidence": 80, "cot_agrees": False,
     "cot_missing": True, "retail_long": 85},
    {"pair": "AUD/USD", "verdict": "SHORT", "confidence": 80, "cot_agrees": False,
     "cot_missing": True, "retail_long": 88},
    {"pair": "USD/JPY", "verdict": "FLAT", "confidence": 40, "retail_long": 55},
]


def test_levels_risk_is_one_sigma():
    lv = zc.levels("SELL", last=158.608, mean=158.238, sd=0.1, z=3.7)
    assert lv["stop"] == round(158.238 + 4.7 * 0.1, 5) and lv["tp2"] == 158.238
    assert lv["r_tp1"] == 2.7 and lv["r_tp2"] == 3.7                 # TP1=|z|-1 R, TP2=|z| R
    assert zc.levels("BUY", 1.0, 1.0, 0, -2.5) is None


def test_grades_from_the_screenshots():
    breaches = [_breach("USD/JPY", 3.70, 158.60849, 158.23849, 0.1),
                _breach("AUD/USD", -3.40, 0.66158, 0.66328, 0.0005),
                _breach("EUR/USD", 2.6, 1.0991, 1.0965, 0.001),
                {"ok": False, "symbol": "XAU/USD"}]
    out = {r["symbol"]: r for r in zc.build(breaches, SIGNALS, "2026-11-17")}
    assert out["USD/JPY"]["grade"] == "B"          # FLAT confluence → z-only
    assert out["AUD/USD"]["grade"] == "X"          # z BUY vs confluence SHORT → conflict
    assert out["EUR/USD"]["grade"] == "A"          # z SELL + confluence SHORT
    assert "XAU/USD" not in out
    assert [r["grade"] for r in zc.build(breaches, SIGNALS, None)] == ["A", "X", "B"]


def test_card_has_levels_confluence_and_lots():
    r = zc.build([_breach("EUR/USD", 2.6, 1.0991, 1.0965, 0.001)], SIGNALS, "2026-11-17")[0]
    txt = zc.card(r)
    assert "Z-setup — EUR/USD" in txt and "🅰️" in txt and "COT missing" in txt
    assert "TP2:" in txt and "*Lots*" in txt and "`2.5k`" in txt


def test_cot_staleness_and_no_cot_cap():
    from services.wildchance_service import cot_stale, cot_latest
    feed = {"cot": {"EUR": [{"d": "2026-11-10"}, {"d": "2026-11-17"}], "JPY": []}}
    assert cot_latest(feed) == "2026-11-17"
    assert not cot_stale(feed, dt.date(2026, 11, 22)) and cot_stale(feed, dt.date(2026, 11, 30))
    assert cot_stale({}) is True
    from wildchance.wildchance_scraper import evaluate
    sig = evaluate("EUR/USD", 1.1, {"long_pct": 85, "short_pct": 15}, {})
    assert sig["confidence"] == 80 and sig["cot_missing"]           # was 94 retail-only


def test_signal_card_has_no_old_lot_line():
    from services.signal_engine import build_manual, format_card
    card = format_card(build_manual("EUR/USD", "short", 1.09701, 1.09801, [1.09246], balance=2500))
    assert "Lot ≤" not in card and "*Lots*" in card


def test_route_and_hourly_job():
    from main import app
    from services.cron_schedule import jobs
    assert "/zconfluence/scan" in {r.path for r in app.routes}
    hourly = [j for j in jobs() if j.name == "hourly"][0]
    assert ("GET", "/zconfluence/scan?notify=true") in hourly.calls
