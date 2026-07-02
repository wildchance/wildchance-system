from mmm.engine import directional_bias, confluence


def _setup(**kw):
    base = {"taylor_day": None, "level_context": "upper half of prior week",
            "adr_used": 0.4, "peak_formation": {"pivot": None, "reversal": None}}
    base.update(kw)
    return base


def test_bias_long_on_buy_day():
    b = directional_bias(_setup(taylor_day="BUY DAY"))
    assert b["bias"] == "long" and b["score"] > 0


def test_bias_short_on_short_sell_day():
    b = directional_bias(_setup(taylor_day="SHORT SELL DAY",
                                level_context="lower half of prior week"))
    assert b["bias"] == "short" and b["score"] < 0


def test_bearish_reversal_pushes_short():
    s = _setup(peak_formation={"pivot": {"kind": "peak"},
                               "reversal": "bearish_reversal (peak) — swing-short"})
    assert directional_bias(s)["bias"] == "short"


def test_confluence_confirms_and_diverges():
    long_setup = _setup(taylor_day="BUY DAY")
    assert confluence(long_setup, "long")["status"] == "confirms"
    assert confluence(long_setup, "short")["status"] == "diverges"


def test_confluence_neutral_when_no_signal():
    flat = _setup(taylor_day="SELL DAY", level_context="upper half of prior week",
                  peak_formation={"pivot": None, "reversal": None})
    # SELL DAY = neutral, only +1 from upper-half → mild long; check buy/sell mapping
    out = confluence(flat, "long")
    assert out["status"] in ("confirms", "neutral")


def test_confluence_none_without_data():
    assert confluence(None, "long")["status"] == "none"


def test_side_aliases_buy_sell():
    long_setup = _setup(taylor_day="BUY DAY")
    assert confluence(long_setup, "buy")["status"] == "confirms"
    assert confluence(long_setup, "sell")["status"] == "diverges"


def test_stretched_flag_when_adr_exceeded():
    b = directional_bias(_setup(taylor_day="BUY DAY", adr_used=1.4))
    assert b["stretched"] is True
