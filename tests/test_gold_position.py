"""Gold swing-position lifecycle: BE trail, TP/SL, weekly time-stop."""

import datetime as dt

from gold import position as pos

OPEN = dt.datetime(2026, 7, 5, 22, 0, tzinfo=dt.timezone.utc)   # Sunday 22:00 open


def _long():
    return {"side": "long", "entry": 4000, "stop": 3960,
            "targets": [4080, 4120, 4160], "be_active": False, "opened_at": OPEN}


def test_swing_deadline_is_monday_2200():
    dl = pos.swing_deadline(OPEN)
    assert dl.weekday() == 0 and dl.hour == pos.TIME_STOP_HOUR_UTC
    assert dl > OPEN


def test_holding_when_no_target_reached():
    a = pos.evaluate(_long(), 4010, OPEN + dt.timedelta(hours=1))
    assert a["close"] is False and a["tp_hit"] == 0


def test_tp1_arms_break_even():
    a = pos.evaluate(_long(), 4085, OPEN + dt.timedelta(hours=2))
    assert a["be_active"] is True and a["tp_hit"] == 1
    assert a["stop"] == 4000 and a["close"] is False


def test_final_target_closes_in_r():
    a = pos.evaluate(_long(), 4200, OPEN + dt.timedelta(hours=3))
    assert a["close"] and a["exit_reason"] == "TP3"
    assert a["result_r"] == 4.0        # (4160-4000)/40


def test_initial_stop_is_minus_one_r():
    a = pos.evaluate(_long(), 3959, OPEN + dt.timedelta(hours=1))
    assert a["close"] and a["exit_reason"] == "SL" and a["result_r"] == -1.0


def test_break_even_scratch():
    state = dict(_long(), be_active=True)
    a = pos.evaluate(state, 3999, OPEN + dt.timedelta(hours=4))
    assert a["close"] and a["exit_reason"] == "BE" and a["result_r"] == 0.0


def test_time_stop_closes_at_market():
    dl = pos.swing_deadline(OPEN)
    a = pos.evaluate(_long(), 4050, dl + dt.timedelta(hours=1))
    assert a["close"] and a["exit_reason"] == "TIME"
    assert a["result_r"] == round(50 / 40, 4)


def test_short_exits_at_target_price():
    sh = {"side": "short", "entry": 4200, "stop": 4240, "targets": [4120],
          "be_active": False, "opened_at": OPEN}
    a = pos.evaluate(sh, 4100, OPEN + dt.timedelta(hours=2))
    # Closes AT the target (4120), not the overshoot price → +2R.
    assert a["close"] and a["exit_price"] == 4120 and a["result_r"] == 2.0
