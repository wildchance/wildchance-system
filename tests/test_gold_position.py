"""HTF timeline identifier — daily named-zone ladder + region/bias location."""

from gold.timeline import htf_ladder, locate, htf_confluence, HTF_ANCHOR


def test_ladder_matches_chart_levels():
    lad = htf_ladder()["levels"]
    assert lad["0"]["price"] == 3885.044 and lad["0"]["zone"] == "central limit"
    assert lad["1"]["price"] == 4381.940 and lad["0.5"]["zone"] == "bullish mean"
    assert abs(lad["1.5"]["price"] - 4630.388) < 0.01 and lad["1.5"]["zone"] == "buy/sell limit (upper)"
    assert abs(lad["2"]["price"] - 4878.836) < 0.01 and lad["2"]["zone"] == "equilibrium (upper)"
    assert abs(lad["4"]["price"] - 5872.628) < 0.01 and lad["4"]["zone"] == "tp4 / scale-out 3"
    assert abs(lad["-3"]["price"] - 2394.356) < 0.01


def test_ladder_spans_minus3_to_plus4():
    lad = htf_ladder()["levels"]
    assert "-3" in lad and "4" in lad and "-3.5" not in lad and "4.5" not in lad


def test_locate_current_price_is_accumulation_long():
    loc = locate(3986.28)                    # k ≈ 0.20
    assert 0.19 < loc["k"] < 0.21
    assert loc["smaller_tf_bias"] == "long" and "discount" in loc["region"]


def test_locate_premium_is_short():
    loc = locate(4950.0)                      # above equilibrium (k≈2.14)
    assert loc["smaller_tf_bias"] == "short"


def test_locate_scale_out_flag_at_tp():
    assert locate(5150.0)["at_scale_out"] is True     # k≈2.55, tp zone


def test_locate_nearest_levels():
    loc = locate(3986.28)
    assert loc["nearest_above"]["zone"] == "bullish mean"      # 4133 above
    assert loc["nearest_below"]["zone"] == "central limit"     # 3885 below


def test_htf_confluence():
    # In discount (accumulation), a long aligns and a short opposes.
    assert htf_confluence("long", 3986.28) == "aligns"
    assert htf_confluence("short", 3986.28) == "opposes"
    # In premium, a short aligns.
    assert htf_confluence("short", 4950.0) == "aligns"


def test_anchor_override():
    lad = htf_ladder(zero=1000.0, one=1100.0)
    assert lad["unit"] == 100.0 and lad["levels"]["1"]["price"] == 1100.0
