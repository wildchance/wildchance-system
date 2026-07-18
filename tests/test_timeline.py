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


# --- weekly decision + cycle replication -------------------------------------

def test_fib_levels_match_chart_rail():
    from gold.timeline import fib_levels
    f = fib_levels()
    assert abs(f["0.236"] - 4002.311) < 0.01
    assert abs(f["0.382"] - 4074.858) < 0.01
    assert abs(f["0.786"] - 4275.604) < 0.01


def test_weekly_decision_buy_sell_wait():
    from gold.timeline import weekly_decision
    buy = weekly_decision(4025.0)
    assert buy["decision"] == "long" and abs(buy["target"] - 4074.858) < 0.01
    sell = weekly_decision(3995.0)
    assert sell["decision"] == "short" and abs(sell["target"] - 3885.044) < 0.01
    assert weekly_decision(4014.26)["decision"] == "wait"      # inside the band


def test_cycle_replication_above_ath():
    from gold.timeline import cycle_status
    assert cycle_status(4014.0)["phase"] == "active"
    nxt = cycle_status(5700.0)                                  # above the 5608 ATH
    assert nxt["phase"] == "next_cycle"
    assert abs(nxt["next_cycle"]["plus4"] - 7509.776) < 0.5     # replicated +4
