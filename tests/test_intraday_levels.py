import datetime as dt

from services import session_levels as sl


def _b(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


def test_session_levels_named_from_ny_bars():
    bars = [_b("2026-11-22 14:00:00", 4000, 4010, 3990, 4005), _b("2026-11-22 20:00:00", 4005, 4030, 4000, 4020),
            _b("2026-11-23 00:00:00", 4020, 4025, 4015, 4018), _b("2026-11-23 01:00:00", 4018, 4022, 4012, 4014),
            _b("2026-11-23 07:00:00", 4014, 4040, 4013, 4035)]
    lv = {x["label"]: x["price"] for x in sl.session(bars)}
    assert lv["midnight open"] == 4020 and lv["midnight high"] == 4025 and lv["1am low"] == 4012
    assert lv["7am high"] == 4040 and lv["8-hour low"] == 4012 and lv["8-hour high"] == 4040
    assert lv["14-hour high"] == 4030 and lv["14-hour low"] == 3990 and lv["prev day high"] == 4030


def test_grid_steps_and_strikes(tmp_path):
    g = {x["label"]: x["price"] for x in sl.grid("XAU/USD", 4475.0)}
    assert g["R1"] == 4490.0 and g["S2"] == 4445.0 and len(g) == 16
    assert abs(sl.grid("EUR/USD", 1.1)[0]["price"] - 1.106) < 1e-9
    f = tmp_path / "s.csv"
    f.write_text("symbol,expiry,put25,call25,put10,call10,source\n"
                 "XAU/USD,2026-12-24,3500,3800,,,inv\nXAU/USD,2026-10-01,1,2,,,old\n")
    st = sl.option_strikes("XAUUSD", dt.date(2026, 11, 23), path=str(f))
    labels = [x["label"] for x in st]
    assert "25Δ put 24-Dec" in labels and "25Δ call 24-Dec" in labels and not any("Oct" in l for l in labels)
    assert sl.near([{"label": "R1", "price": 4490.0}], 4485, 4488, 3) == ["R1"]
