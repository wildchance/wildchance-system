import datetime as dt

from gold.quarterly_session import session_quarter, is_trade_window, weekday_quarter
from gold.hurst import fld, fld_signal, nominal_model, vtl, vtl_broken
from gold.intraday import assemble_intraday, format_card


# ---------------- Quarterly-Theory session ----------------
def _t(h):  # UTC datetime at hour h (a Tuesday)
    return dt.datetime(2026, 7, 7, h, 0, tzinfo=dt.timezone.utc)


def test_session_quarters():
    assert session_quarter(_t(2))["quarter"] == 1      # Asia accumulation
    assert session_quarter(_t(8))["quarter"] == 2      # London manipulation
    assert session_quarter(_t(14))["quarter"] == 3     # NY-AM distribution
    assert session_quarter(_t(20))["quarter"] == 4     # NY-PM
    assert session_quarter(_t(14))["is_distribution"] is True


def test_trade_window():
    assert is_trade_window(_t(14)) is True             # Q3
    assert is_trade_window(_t(8)) is True              # Q2 allowed
    assert is_trade_window(_t(2)) is False             # Q1 accumulation


def test_weekday_quarter():
    assert weekday_quarter(_t(14))["quarter"] == 2 and weekday_quarter(_t(14))["tradeable"]
    fri = dt.datetime(2026, 7, 10, 14, tzinfo=dt.timezone.utc)
    assert weekday_quarter(fri)["tradeable"] is False


# ---------------- Hurst FLD / VTL ----------------
def test_fld_alignment():
    f = fld([1, 2, 3, 4, 5, 6], 4)     # half=2 → f[t]=price[t-2]
    assert f[0] is None and f[2] == 1 and f[5] == 4


def test_fld_bull_and_bear_cross():
    assert fld_signal([10, 10, 10, 10, 9, 12], 4)["cross"] == "bull"
    assert fld_signal([10, 10, 10, 10, 11, 8], 4)["cross"] == "bear"


def test_nominal_model_harmonic():
    d = nominal_model()["days"]
    assert d[1] == 2 * d[0] and d[2] == 2 * d[1]       # ×2 ladder


def test_vtl_and_break():
    prices = [10, 8, 6, 8, 10, 9, 7, 9, 11]
    line = vtl(prices, "low")
    assert line and line["kind"] == "low"
    assert vtl_broken([10, 8, 6, 8, 10, 9, 7, 9, 5], line) is True   # last price breaks below


# ---------------- Intraday fusion ----------------
def _profile(bias="long"):
    return {"bias": bias, "profile": "Classic Tuesday Low", "reason": "Tue low into discount",
            "zone": "discount"}

_Q3 = {"quarter": 3, "phase": "distribution", "session": "New York AM"}
_Q1 = {"quarter": 1, "phase": "accumulation", "session": "Asia"}
_BULL = {"cross": "bull", "position": "above", "position_dir": "bull"}
_BEAR = {"cross": "bear", "position": "below", "position_dir": "bear"}


def test_intraday_fires_when_all_align():
    s = assemble_intraday(_profile("long"), _Q3, _BULL, 4316.0, 5000.0)
    assert s["signal"] == "LONG" and s["fld_confirms"] is True and "justification" in s


def test_intraday_waits_wrong_quarter():
    s = assemble_intraday(_profile("long"), _Q1, _BULL, 4316.0, 5000.0)
    assert s["signal"] == "NO TRADE" and "quarter" in s["reason"]


def test_intraday_blocked_by_fld_divergence():
    s = assemble_intraday(_profile("long"), _Q3, _BEAR, 4316.0, 5000.0, require_fld=True)
    assert s["signal"] == "NO TRADE" and "FLD" in s["reason"]


def test_intraday_no_trade_on_neutral_profile():
    s = assemble_intraday({"bias": "neutral", "profile": "Seek and Destroy"}, _Q3, _BULL,
                          4316.0, 5000.0)
    assert s["signal"] == "NO TRADE"


def test_intraday_friday_stand_aside():
    s = assemble_intraday(_profile("long"), _Q3, _BULL, 4316.0, 5000.0,
                          weekday_q={"tradeable": False, "quarter": 0})
    assert s["signal"] == "NO TRADE" and "Friday" in s["reason"]


def test_intraday_card_has_layer_line():
    s = assemble_intraday(_profile("long"), _Q3, _BULL, 4316.0, 5000.0)
    card = format_card(s)
    assert "GOLD XAU/USD" in card and "Q3" in card and "FLD" in card
