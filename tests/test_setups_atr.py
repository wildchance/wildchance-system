"""ATR overlay on the setup builder (Phase A: ATR into allocation)."""

import pytest

from cbdr.engine import build_cbdr
from setups.engine import build_setup


def _box():
    return build_cbdr(11.0, 10.0)          # range 1.0, mid 10.5


def test_no_atr_is_backward_compatible():
    s = build_setup(_box(), "long", entry=11.0, mode="continuation")
    assert "atr" not in s                    # nothing added when atr not passed
    assert s["stop_loss"] == pytest.approx(9.5)


def test_atr_floors_a_tight_stop():
    # continuation long stop = box.low - 0.5*range = 9.5 → distance 1.5 from entry 11.
    # ATR 2.0 * mult 1.5 = 3.0 > 1.5 → stop widens to entry-3.0 = 8.0
    s = build_setup(_box(), "long", entry=11.0, mode="continuation",
                    atr=2.0, atr_mult=1.5)
    assert s["atr"]["stop_floored_to_atr"] is True
    assert s["stop_loss"] == pytest.approx(8.0)
    assert s["risk_per_unit"] == pytest.approx(3.0)


def test_atr_does_not_tighten_a_wide_stop():
    # tiny ATR → atr_dist 0.15 < existing 1.5 → stop unchanged
    s = build_setup(_box(), "long", entry=11.0, mode="continuation",
                    atr=0.1, atr_mult=1.5)
    assert s["atr"]["stop_floored_to_atr"] is False
    assert s["stop_loss"] == pytest.approx(9.5)


def test_atr_targets_present_and_directional():
    s = build_setup(_box(), "short", entry=10.0, mode="continuation",
                    atr=1.0, atr_mult=1.5)
    # short ATR targets step DOWN from entry
    assert s["atr"]["targets"] == [9.0, 8.0, 7.0]


def test_spike_adds_entry_warning():
    s = build_setup(_box(), "long", entry=11.0, mode="continuation",
                    atr=2.0, spike=True)
    assert s["spike"] is True and "expansion" in s["entry_warning"]
    s2 = build_setup(_box(), "long", entry=11.0, mode="continuation",
                     atr=2.0, spike=False)
    assert s2["spike"] is False and "entry_warning" not in s2


def test_r_multiples_reflect_widened_stop():
    # after flooring to stop 8.0 (risk 3.0), TP at +1SD (12.0) → R = 1.0/3.0
    s = build_setup(_box(), "long", entry=11.0, mode="continuation",
                    atr=2.0, atr_mult=1.5)
    tp1 = s["targets"][0]
    assert tp1["r"] == pytest.approx(abs(tp1["price"] - 11.0) / 3.0, rel=1e-2)
