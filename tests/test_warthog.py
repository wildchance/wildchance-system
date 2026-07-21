"""Warthog — HTF sweep + OTE catapult, and its recon confluence wiring."""

from gold.warthog import warthog, detect_sweep, to_ohlc, format_warthog
from gold import recon as gr


def _bar(o, h, l, c):
    return (o, h, l, c)


def _bearish_series():
    """A downtrend: lower highs/lows, a sweep of a swing low, then a retrace up."""
    return [
        _bar(4100, 4105, 4090, 4095),
        _bar(4095, 4098, 4070, 4075),
        _bar(4075, 4080, 4055, 4060),   # swing low ~4055
        _bar(4060, 4072, 4058, 4068),
        _bar(4068, 4074, 4060, 4064),
        _bar(4064, 4066, 4030, 4035),   # breaks/sweeps below 4055 → bearish BMS
        _bar(4035, 4055, 4033, 4052),   # retrace UP into OTE of the down-leg
        _bar(4052, 4058, 4048, 4050),
    ]


def test_detect_sweep_finds_low():
    sw = detect_sweep(_bearish_series())
    assert sw is not None and sw["type"] in ("low", "high")


def test_warthog_bearish_continuation():
    r = warthog(_bearish_series())
    assert r["signal"] == "SHORT"
    assert r["trend"] == "bearish"
    assert r["entry"] is not None and r["stop"] is not None
    assert r["stop"] > r["entry"]                 # short stop above entry
    assert isinstance(r["targets"], list) and r["targets"][0]["rr"] == 2.0


def test_warthog_none_without_trend():
    flat = [_bar(4000 + (i % 2), 4002, 3998, 4000) for i in range(10)]
    r = warthog(flat)
    assert r["signal"] == "NONE"


def test_to_ohlc_from_raw_dicts():
    raw = [{"time": "t", "open": 1, "high": 2, "low": 0.5, "close": 1.5}]
    assert to_ohlc(raw) == [(1.0, 2.0, 0.5, 1.5)]


def test_format_warthog_line():
    r = warthog(_bearish_series())
    txt = format_warthog(r)
    assert txt is not None and "WARTHOG" in txt and "SHORT" in txt


def test_recon_folds_in_warthog():
    from cbdr.engine import build_cbdr
    box = build_cbdr(4030.0, 4010.0)          # +1SD=4050, +1.5SD=4060
    wh = {"signal": "SHORT", "sweep": {"type": "high", "level": 4065.0},
          "retracement": 0.7, "in_ote": True, "catapult": True}
    # price inside the sell_4045_4065 OB and at the +1SD extreme → SHORT anchor
    sweep = gr.recon_sweep(4055.0, dxy_price=100.75, box=box, warthog=wh)
    sell = next((s for s in sweep["setups"] if s["side"] == "SHORT"), None)
    assert sell is not None and sell["warthog_confluence"] is True
    assert "🐗" in gr.format_recon(sweep)
