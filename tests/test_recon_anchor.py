"""Batch 2: zone-anchored entry (weekly OB + CBDR ±SD extreme) + drone recon sweep."""

import pytest

from gold import zones as gz
from gold import recon as gr
from cbdr.engine import build_cbdr


# --- weekly OB recalibration -------------------------------------------------

def test_weekly_ob_zone_is_3250_3500():
    z = next(x for x in gz.ZONES if x["side"] == "buy" and x["low"] <= 3300 <= x["high"])
    assert z["name"] == "weekly_ob_3250_3500"
    assert z["low"] == 3250.0 and z["high"] == 3500.0


# --- zone-anchored entry (OB + CBDR deviation extreme) -----------------------

def test_anchor_needs_ob_membership():
    # 4018 is between shelves — not inside any buy OB → not anchored
    a = gz.zone_anchored_entry("long", 4018.0)
    assert a["in_ob"] is False and a["ok"] is False


def test_anchor_ob_only_without_box():
    # inside the weekly OB, no CBDR box → OB factor alone passes
    a = gz.zone_anchored_entry("long", 3300.0)
    assert a["in_ob"] is True and a["ok"] is True
    assert a["ob_zone"] == "weekly_ob_3250_3500"


def test_anchor_requires_cbdr_extreme_when_box_given():
    box = build_cbdr(3320.0, 3300.0)          # 1SD=20 → -1SD=3280, -1.5SD=3270
    # price inside the OB but ABOVE the −1SD extreme → staged, not armed
    a_high = gz.zone_anchored_entry("long", 3305.0, box)
    assert a_high["in_ob"] is True and a_high["at_cbdr_extreme"] is False and a_high["ok"] is False
    # price at/below −1SD → anchored (armed on the deviation map)
    a_deep = gz.zone_anchored_entry("long", 3275.0, box)
    assert a_deep["at_cbdr_extreme"] is True and a_deep["ok"] is True
    assert a_deep["cbdr_level"] == "-1SD"
    # below −1.5SD → the deepest extreme label
    a_deepest = gz.zone_anchored_entry("long", 3268.0, box)
    assert a_deepest["cbdr_level"] == "-1.5SD"


# --- drone recon sweep -------------------------------------------------------

def test_recon_buy_staged_while_dxy_locked():
    # gold anchored in the weekly OB at the −1.5SD extreme, but DXY at 100.75 →
    # longs LOCKED → the buy setup is STAGED, not armed.
    box = build_cbdr(3320.0, 3300.0)
    sweep = gr.recon_sweep(3268.0, dxy_price=100.75, box=box)
    buy = next(s for s in sweep["setups"] if s["side"] == "LONG")
    assert buy["armed"] is False
    assert "LOCKED" in buy["gate"]
    assert sweep["dxy"]["gold_longs"] == "locked"


def test_recon_buy_arms_after_flip():
    box = build_cbdr(3320.0, 3300.0)
    # override the DXY flip to unlocked → the anchored buy arms
    from gold import dxy as gdxy
    sweep = gr.recon_sweep(3268.0, dxy_price=106.0, box=box, rbusbis_dir="falling")
    buy = next((s for s in sweep["setups"] if s["side"] == "LONG"), None)
    assert buy is not None and buy["armed"] is True
    assert sweep["armed"] is True


def test_recon_report_has_both_instruments():
    sweep = gr.recon_sweep(4018.0, dxy_price=100.75)
    assert "gold" in sweep and "dxy" in sweep
    txt = gr.format_recon(sweep)
    assert "GOLD/DXY RECON" in txt
    assert "DXY" in txt and "longs" in txt


def test_recon_dxy_nearest_fib():
    sweep = gr.recon_sweep(4018.0, dxy_price=100.75)
    nf = sweep["dxy"]["nearest_fib"]
    assert nf["above"] is not None and nf["below"] is not None
    assert nf["above"]["price"] > 100.75 >= nf["below"]["price"]
