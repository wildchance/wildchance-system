"""1-5-9 CRT model + FRED/CFTC parse helpers."""

from candlerange.crt import (crt_levels, crt_setup, crt_confirm,
                             session_for_hour, CRT_DEVIATIONS)
from services.fred_service import parse_series, direction
from services.cftc_service import net_and_oi


# --- CRT ---------------------------------------------------------------------

_CANDLE = {"open": 100.0, "high": 112.0, "low": 98.0, "close": 110.0}  # body 100-110


def test_crt_levels_project_from_body():
    lv = crt_levels(_CANDLE)
    assert lv["body_low"] == 100 and lv["body_high"] == 110 and lv["range"] == 10
    assert lv["+1"] == 120 and lv["-1"] == 90 and lv["+3"] == 140 and lv["-3"] == 70


def test_crt_setup_arms_buy_minus1_sell_plus1_plus3():
    s = crt_setup(_CANDLE, "asia")
    assert s["ok"] and s["confirm_hour"] == 5 and s["target_hour"] == 9
    by = {(o["side"], o["dev"]): o["entry"] for o in s["orders"]}
    assert by[("long", -1.0)] == 90
    assert by[("short", 1.0)] == 120 and by[("short", 3.0)] == 140


def test_crt_setup_ny_hours():
    s = crt_setup(_CANDLE, "ny")
    assert s["range_hour"] == 13 and s["confirm_hour"] == 17 and s["target_hour"] == 21


def test_crt_confirm_triggers_at_limit():
    s = crt_setup(_CANDLE, "asia")
    c = crt_confirm(s, 90.4)                 # within 10% of range (tol 1.0) of −1 @ 90
    assert c["confirmed"] and c["order"]["side"] == "long" and c["order"]["dev"] == -1.0


def test_crt_confirm_no_trigger_midrange():
    s = crt_setup(_CANDLE, "asia")
    assert crt_confirm(s, 105.0)["confirmed"] is False


def test_crt_flat_candle_no_setup():
    assert crt_setup({"open": 100, "high": 100, "low": 100, "close": 100}, "asia")["ok"] is False


def test_session_for_hour():
    assert session_for_hour(5) == "asia" and session_for_hour(1) == "asia"
    assert session_for_hour(17) == "ny" and session_for_hour(13) == "ny"
    assert session_for_hour(3) is None


def test_crt_deviations_include_half_and_three():
    assert 0.5 in CRT_DEVIATIONS and 3.0 in CRT_DEVIATIONS


# --- FRED / CFTC parse -------------------------------------------------------

def test_fred_parse_skips_gaps():
    assert parse_series([{"value": "1.5"}, {"value": "."}, {"value": "2.0"}]) == [1.5, 2.0]


def test_fred_direction():
    assert direction([1.0, 2.0, 3.0]) == "rising"
    assert direction([3.0, 2.0, 1.0]) == "falling"
    assert direction([2.0, 2.0, 2.0]) == "flat"


def test_cftc_net_and_oi():
    row = {"noncomm_positions_long_all": "200000",
           "noncomm_positions_short_all": "50000",
           "open_interest_all": "400000",
           "report_date_as_yyyy_mm_dd": "2026-06-23T00:00:00.000"}
    r = net_and_oi(row)
    assert r["noncomm_net"] == 150000 and r["open_interest"] == 400000
    assert r["report_date"] == "2026-06-23"


def test_cftc_bad_row_returns_none():
    assert net_and_oi({"noncomm_positions_long_all": "x"}) is None
