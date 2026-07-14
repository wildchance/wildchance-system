"""Cross-session CBDR confluence engine + the Asian→London backtest."""

from cbdr.engine import build_cbdr
from cbdr.confluence import cross_session_confluence, conviction, _bias_num
from backtest.cbdr_confluence_backtest import backtest


def _bar(day, h, o, hi, lo, c):
    # matches services.ohlc_service.fetch_hourly_raw: {date, hour, open, high, low, close}
    return {"date": f"2026-07-{day:02d}", "hour": h,
            "open": o, "high": hi, "low": lo, "close": c}


# ---- confluence engine -----------------------------------------------------

def test_regime_gate_suppresses_premium_sell():
    asian = build_cbdr(4020, 4000)     # +1SD premium = 4040
    london = build_cbdr(3970, 3950)    # -1SD discount = 3930
    # weekly SHORT but macro NEUTRAL → NOT a confirmed downtrend → sell SUPPRESSED
    r = cross_session_confluence(asian, london, weekly_bias="short", macro_bias="neutral")
    assert r["orders"] == []
    assert r["regime"]["enable_sell"] is False
    # CONFIRMED downtrend (weekly AND macro short) → the sell arms
    r2 = cross_session_confluence(asian, london, weekly_bias="short", macro_bias="short")
    assert len(r2["orders"]) == 1 and r2["orders"][0]["side"] == "short"
    # force it on regardless of regime
    r3 = cross_session_confluence(asian, london, "short", "neutral", enable_sell=True)
    assert r3["orders"] and r3["orders"][0]["side"] == "short"


def test_buy_discount_arms_in_long_weeks():
    asian, london = build_cbdr(4020, 4000), build_cbdr(3970, 3950)
    # weekly LONG → the proven discount BUY arms (buy is on by default)
    buy = cross_session_confluence(asian, london, weekly_bias="long")["orders"]
    assert len(buy) == 1 and buy[0]["side"] == "long"


def test_sell_targets_ladder_into_london_discount():
    asian = build_cbdr(4020, 4000)
    london = build_cbdr(3970, 3950)
    # force the sell on to inspect its geometry
    o = cross_session_confluence(asian, london, "short", "short",
                                 enable_sell=True)["orders"][0]
    assert o["entry"] == 4040                     # Asian +1SD premium
    assert o["stop"] > 4040                         # beyond +2SD
    assert o["targets"][-1] < o["targets"][0]       # ladders DOWN
    assert min(o["targets"]) <= 3930                # reaches London discount / deeper


def test_macro_and_geometry_and_conviction():
    assert conviction(85) == "A" and conviction(70) == "B" and conviction(55) == "C"
    assert conviction(40) == "-"
    assert _bias_num("bullish") == 1 and _bias_num("short") == -1 and _bias_num(None) == 0
    # macro agreeing with a down fade lifts the score above weekly-only (force sells on)
    asian, london = build_cbdr(4020, 4000), build_cbdr(3970, 3950)
    both = cross_session_confluence(asian, london, "short", "short",
                                    enable_sell=True)["orders"][0]
    wk_only = cross_session_confluence(asian, london, "short", "neutral",
                                       enable_sell=True)["orders"][0]
    assert both["score"] > wk_only["score"]


def test_min_score_filters_low_conviction():
    asian, london = build_cbdr(4020, 4000), build_cbdr(3970, 3950)
    # neutral everything → mid score; a high threshold rejects it
    assert cross_session_confluence(asian, london, "neutral", "neutral",
                                    min_score=90)["orders"] == []


def test_falls_back_to_asian_downside_without_london():
    asian = build_cbdr(4020, 4000)
    o = cross_session_confluence(asian, None, "short", "short",
                                 enable_sell=True)["orders"][0]
    # targets come from the Asian box's own downside SD (−1/−2/−3SD)
    assert o["targets"][1] == 3980 and o["targets"][-1] == 3940


# ---- backtest --------------------------------------------------------------

def _down_day(day):
    """Asian coil 4000-4020, then price sweeps +1SD (4040) and drops to 3960."""
    asia = [_bar(day, h, 4010, 4020, 4000, 4010) for h in range(8)]
    london = [
        _bar(day, 8, 4020, 4045, 4030, 4035),      # sweeps the 4040 sell limit
        _bar(day, 9, 4035, 4038, 4010, 4015),
        _bar(day, 10, 4015, 4020, 3955, 3960),     # drops to the 3960 target → win
        _bar(day, 11, 3960, 3965, 3950, 3958),
    ]
    return asia + london


def test_backtest_records_win_and_splits_by_bias():
    days = {"2026-07-06": _down_day(6), "2026-07-07": _down_day(7)}
    wk = {"2026-07-06": "short", "2026-07-07": "short"}
    # regime_gated=False forces the sell on so we can test the sell mechanics
    r = backtest(days, wk, min_score=50, regime_gated=False)
    ov = r["overall"]
    assert ov["filled"] >= 2 and ov["wins"] >= 2
    assert ov["hit_rate"] == 1.0
    assert ov["avg_win_pips"] and ov["avg_win_pips"] > 400        # big cross-session move
    assert r["by_bias"]["short"]["wins"] >= 2
    assert r["by_bias"]["long"]["settled"] == 0                    # no long fades on short days


def test_backtest_regime_gate_suppresses_shorts():
    # same down-days, but the DEFAULT regime gate suppresses the (−EV) premium-sell
    days = {"2026-07-06": _down_day(6), "2026-07-07": _down_day(7)}
    wk = {"2026-07-06": "short", "2026-07-07": "short"}
    r = backtest(days, wk, min_score=50)                          # regime_gated=True
    assert r["overall"]["trades"] == 0                            # no shorts armed
    assert r["params"]["regime_gated"] is True


def test_backtest_empty_when_no_asian_box():
    # a day with too few Asian bars → skipped, no trades
    thin = {"2026-07-06": [_bar(6, 8, 4000, 4010, 3990, 4005)]}
    r = backtest(thin, {"2026-07-06": "short"})
    assert r["overall"]["trades"] == 0


def test_stop_sd_tightens_the_stop():
    # buy at London -1SD; a smaller stop_sd puts the stop CLOSER to entry → less risk
    london = build_cbdr(4020, 4000)                       # range 20, -1SD entry = 3980
    wide = cross_session_confluence(london, None, "long", "long", stop_sd=2.0)["orders"][0]
    tight = cross_session_confluence(london, None, "long", "long", stop_sd=1.5)["orders"][0]
    assert tight["stop"] > wide["stop"]                    # 1.5SD stop is higher (closer)
    assert (tight["entry"] - tight["stop"]) < (wide["entry"] - wide["stop"])


def test_backtest_reports_train_test_split():
    days = {f"2026-07-{d:02d}": _down_day(d) for d in (6, 7, 8, 9)}
    wk = {d: "short" for d in days}
    r = backtest(days, wk, min_score=50, regime_gated=False)
    assert "train" in r and "test" in r
    assert r["test"]["from"] is not None                  # split point set
    # every settled trade lands in exactly one of train/test
    assert r["train"]["settled"] + r["test"]["settled"] == r["overall"]["settled"]
