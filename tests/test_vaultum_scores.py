"""VAULTUM feature-score board — standardised scores + composite gold bias."""

from gold import vaultum_scores as vs


# --- individual score envelopes ---------------------------------------------------

def test_envelope_shape_and_clamp():
    e = vs.dollar_strength_score(regime="strong")
    assert set(e) == {"value", "confidence", "drivers", "explanation"}
    assert 0 <= e["value"] <= 100 and 0.0 <= e["confidence"] <= 1.0


def test_missing_input_degrades_to_neutral_low_conf():
    e = vs.inflation_pressure_score()
    assert e["value"] == 50.0 and e["confidence"] <= 0.2


def test_dollar_is_inverse_gold():
    strong = vs.dollar_strength_score(regime="strong")
    weak = vs.dollar_strength_score(regime="weak")
    assert weak["value"] > strong["value"]        # weak dollar = bullish gold


def test_real_rates_dominate_inflation_score():
    falling = vs.inflation_pressure_score(real_rate_direction="falling")
    rising = vs.inflation_pressure_score(real_rate_direction="rising")
    assert falling["value"] > 50 > rising["value"]


def test_macro_cycle_maps_bias():
    assert vs.macro_cycle_score("bullish", "high")["value"] > 60
    assert vs.macro_cycle_score("bearish", "high")["value"] < 40


def test_market_stress_rises_with_vix():
    assert vs.market_stress_score(vix=32)["value"] > vs.market_stress_score(vix=12)["value"]


# --- the composite board ----------------------------------------------------------

def _bullish_scores():
    return {
        "dollar_strength": vs.dollar_strength_score(regime="weak"),
        "macro_cycle": vs.macro_cycle_score("bullish", "high"),
        "inflation_pressure": vs.inflation_pressure_score(real_rate_direction="falling"),
        "market_stress": vs.market_stress_score(vix=26),
        "vol_regime": vs.vol_regime_score(regime="expansion"),
        "venom_phase": vs.venom_phase_score({"confluence": {"conviction": "high",
                                             "timeframes_aligned": 3}, "intraday": {"phase": "distribution"}}),
    }


def test_board_bullish_reads_long():
    b = vs.gold_bias_board(_bullish_scores())
    assert b["direction"] == "long" and b["gold_bias"] > 56
    assert 0 <= b["conviction_pct"] <= 100
    assert b["top_drivers"] and b["explanation"]


def test_board_bearish_reads_short():
    scores = {
        "dollar_strength": vs.dollar_strength_score(regime="strong"),
        "macro_cycle": vs.macro_cycle_score("bearish", "high"),
        "inflation_pressure": vs.inflation_pressure_score(real_rate_direction="rising"),
        "market_stress": vs.market_stress_score(vix=12),
    }
    b = vs.gold_bias_board(scores)
    assert b["direction"] == "short" and b["gold_bias"] < 44


def test_board_empty_is_neutral_not_crash():
    b = vs.gold_bias_board({})
    assert b["direction"] == "neutral" and b["gold_bias"] == 50.0


def test_format_board_line():
    line = vs.format_board(vs.gold_bias_board(_bullish_scores()))
    assert "VAULTUM" in line and "bias" in line


def test_volume_location_score_directional():
    bull = vs.volume_location_score("above_value", "above")
    bear = vs.volume_location_score("below_value", "below")
    neutral = vs.volume_location_score("in_value", "above")
    assert bull["value"] > 55 and bear["value"] < 45
    assert neutral["value"] == 50.0
    assert vs.volume_location_score()["value"] == 50.0     # no read → neutral
