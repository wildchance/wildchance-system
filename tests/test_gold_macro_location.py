"""Gold macro anchor + premium/discount location gate."""

from gold import macro as m
from gold import location as loc


# --- macro confluence -------------------------------------------------------

def test_macro_bias_is_bullish_base_case():
    assert m.macro_bias() == "long"


def test_long_in_accumulation_confirms():
    c = m.macro_confluence("long", 4050)
    assert c["status"] == "confirms"
    assert c["in_accumulation"] is True
    assert c["invalidated"] is False


def test_short_diverges_from_bull_anchor():
    assert m.macro_confluence("short", 4050)["status"] == "diverges"


def test_below_invalidation_flags():
    c = m.macro_confluence("long", 3850)
    assert c["invalidated"] is True


def test_not_in_accumulation_when_extended():
    assert m.in_accumulation(4300) is False
    assert m.in_accumulation(4050) is True


# --- location gate ----------------------------------------------------------

def test_long_allowed_only_in_discount():
    g = loc.location_gate("long", 4020, high=4200, low=4000)
    assert g["ok"] is True and g["zone"] == "discount"

    blocked = loc.location_gate("long", 4180, high=4200, low=4000)
    assert blocked["ok"] is False and blocked["zone"] == "premium"


def test_short_allowed_only_in_premium():
    g = loc.location_gate("short", 4180, high=4200, low=4000)
    assert g["ok"] is True and g["zone"] == "premium"

    blocked = loc.location_gate("short", 4020, high=4200, low=4000)
    assert blocked["ok"] is False


def test_deep_discount_tightening():
    # pos 0.45 is in the discount half but not the cheapest third.
    g = loc.location_gate("long", 4090, high=4200, low=4000, deep=0.33)
    assert g["ok"] is False


def test_degenerate_range_skips_gate():
    g = loc.location_gate("long", 4100, high=4100, low=4100)
    assert g["ok"] is True


def test_in_zone_helper():
    assert loc.in_zone(4050, [4000, 4100]) is True
    assert loc.in_zone(4200, [4000, 4100]) is False
    assert loc.in_zone(None, [4000, 4100]) is False
