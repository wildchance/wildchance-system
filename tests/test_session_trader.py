import datetime as dt

from services import session_trader as st
from services import zconfluence as zc


def _bars(start, n, step_min, rows):
    t0 = dt.datetime.fromisoformat(start)
    return [{"time": (t0 + dt.timedelta(minutes=step_min * i)).isoformat(sep=" "), "open": o, "high": h, "low": l,
             "close": c} for i, (o, h, l, c) in enumerate(rows[:n])]


def test_trend_state_on_monday_ladder():
    mon = (1.0890, 1.0912)                       # Monday box, range 22 pips
    d = dt.date(2026, 11, 24)
    lower = {d: (1.0860, 1.0880)}                # Tuesday box below Monday low
    assert st.trend_state(mon, lower, "SELL")["state"] == "CONTINUING"
    higher = {d: (1.0905, 1.0925)}               # back above Monday's mean
    assert st.trend_state(mon, higher, "SELL")["state"] == "REVERSING"
    inside = {d: (1.0893, 1.0905)}
    assert st.trend_state(mon, inside, "SELL")["state"] == "PAUSING"
    assert st.trend_state(None, {}, "SELL")["state"] == "UNKNOWN"


def test_asian_box_and_sweep_trigger():
    rows = [(1.0910, 1.0915, 1.0905, 1.0910)] * 31          # 19:00 → 02:45, 15m
    bars = _bars("2026-11-23 19:00:00", 31, 15, rows)
    box = st.asian_box(bars, dt.date(2026, 11, 24))
    assert box and box["high"] == 1.0915 and box["low"] == 1.0905 and round(box["levels"]["+1SD"], 4) == 1.0925
    after = _bars("2026-11-24 03:00:00", 3, 15, [(1.0912, 1.0921, 1.0910, 1.0918),     # sweep above high
                                                  (1.0918, 1.0919, 1.0908, 1.0911)])   # close back inside
    trig = st.find_trigger(after, box, "SELL")
    assert trig["kind"] == "SWEEP" and trig["entry"] == 1.0911 and trig["extreme"] == 1.0921
    s = st.setup("EUR/USD", "SELL", box, trig, [{"price": 1.0877, "label": "25Δ put 25-Nov"}])
    assert s["stop"] > 1.0921 and s["targets"][0]["price"] < s["entry"]
    assert any("25Δ put" in t["label"] for t in s["targets"])


def test_breakout_trigger_with_bias():
    box = {"high": 1.0915, "low": 1.0905, "mid": 1.0910, "range": 0.001,
           "levels": {"+1SD": 1.0925, "-1SD": 1.0895, "+2SD": 1.0935, "-2SD": 1.0885}}
    after = _bars("2026-11-24 03:00:00", 1, 15, [(1.0905, 1.0906, 1.0890, 1.0892)])
    assert st.find_trigger(after, box, "SELL")["kind"] == "BREAKOUT"
    assert st.find_trigger(after, box, "BUY") is None


def test_expiry_levels(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("symbol,expiry,put25,call25,put10,call10,source\n"
                 "EURUSD,2026-11-25,1.0877,1.0943,1.0842,1.0973,x (O/N ATM 1.0911 IV 8.65%)\n"
                 "EURUSD,2026-12-08,1.0824,1.1006,,,x\n")
    lv = st.expiry_levels("EUR/USD", dt.date(2026, 11, 24), str(p))
    assert [x["label"] for x in lv] == ["pin 25-Nov", "25Δ put 25-Nov", "25Δ call 25-Nov"]
    assert lv[0]["price"] == 1.0911


def test_z_stop_floor_from_atr():
    lv = zc.levels("SELL", 1.0940, 1.0920, 0.0005, 2.0, "", min_risk=0.0018)
    assert round(lv["stop"] - lv["entry"], 4) == 0.0018            # pushed out from 1σ (5 pips) to 18 pips
    bars = [(None, 1, 1.0 + 0.01, 1.0, 1.0)] * 16
    assert round(zc.atr(bars), 4) == 0.01
