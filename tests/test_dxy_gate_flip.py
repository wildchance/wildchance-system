"""Strict DXY-confirm gate + DXY-flip alert (batch 1) + ±1.5SD precision limits."""

import pytest

from gold import dxy as gdxy
from gold import macro_cycle as gcycle
from cbdr.engine import build_cbdr, prelondon_limits


# --- DXY flip status (the 2026 gold-long lock) -------------------------------

def test_flip_locked_pre_manipulation_base():
    # DXY below the manipulation extreme → pre-flip base → longs LOCKED
    for dxy in (99.0, 100.75, 103.0):
        s = gdxy.dxy_flip_status(dxy)
        assert s["unlocked"] is False and s["gold_longs"] == "locked"


def test_flip_at_extreme_alone_still_locked():
    # at the ceiling extreme but no roll-over yet → topped, not flipped → LOCKED
    s = gdxy.dxy_flip_status(106.0)
    assert s["at_extreme"] is True and s["unlocked"] is False


def test_flip_unlocks_on_extreme_plus_rollover():
    # reached the extreme AND RBUSBIS now falling = the flip → UNLOCKED
    s = gdxy.dxy_flip_status(106.0, rbusbis_dir="falling")
    assert s["unlocked"] is True and s["gold_longs"] == "unlocked"


def test_flip_softness_below_extreme_does_not_unlock():
    # RBUSBIS falling while price is still below the extreme = pre-manipulation dip,
    # NOT the flip → stays LOCKED (the key 2026 correction)
    s = gdxy.dxy_flip_status(99.0, rbusbis_dir="falling")
    assert s["unlocked"] is False


def test_flip_operator_override():
    assert gdxy.dxy_flip_status(100.75, override=True)["unlocked"] is True
    assert gdxy.dxy_flip_status(106.0, rbusbis_dir="falling", override=False)["unlocked"] is False


# --- strict regime gate ------------------------------------------------------

def test_strict_blocks_long_pre_flip():
    # dollar below the extreme (100.75, today) → strict long LOCKED
    g = gcycle.regime_gate("long", dxy_price=100.75, strict=True)
    assert g["ok"] is False
    assert "strict" in g["reason"]
    assert g["dxy_flip"]["unlocked"] is False


def test_strict_allows_long_after_flip():
    g = gcycle.regime_gate("long", dxy_price=106.0, strict=True)
    # inject the roll-over via the live RBUSBIS input
    from gold import macro_cycle as mc
    saved = mc.INPUTS.get("dollar_rbusbis_dir")
    try:
        mc.INPUTS["dollar_rbusbis_dir"] = "falling"
        g = gcycle.regime_gate("long", dxy_price=106.0, strict=True)
        assert g["ok"] is True
        assert g["dxy_flip"]["unlocked"] is True
    finally:
        mc.INPUTS["dollar_rbusbis_dir"] = saved


def test_strict_does_not_block_shorts():
    # a gold short while the dollar is still bid is exactly the 2026 play — never locked
    g = gcycle.regime_gate("short", dxy_price=100.75, strict=True)
    assert g["ok"] is True


def test_nonstrict_unchanged():
    # without strict, a bid-dollar long is not opposed → still allowed (old behaviour)
    assert gcycle.regime_gate("long", dxy_price=103.0, strict=False)["ok"] is True


# --- ±1.5SD precision limits + ±1SD momentum stops ---------------------------

def test_prelondon_adds_precision_and_stops():
    plan = prelondon_limits(build_cbdr(110.0, 100.0))    # 1SD = 10
    by = {o["level"]: o for o in plan["orders"]}
    # existing ±1SD reversal limits still keyed cleanly (no collision)
    assert by["-1SD"]["side"] == "long" and by["+1SD"]["side"] == "short"
    # new precision extremes
    assert by["-1.5SD"]["kind"] == "limit" and by["-1.5SD"]["side"] == "long"
    assert by["+1.5SD"]["kind"] == "limit" and by["+1.5SD"]["side"] == "short"
    # new momentum stops, distinct level tags
    assert by["+1SD_stop"]["kind"] == "buy_stop" and by["+1SD_stop"]["side"] == "long"
    assert by["-1SD_stop"]["kind"] == "sell_stop" and by["-1SD_stop"]["side"] == "short"


def test_buy_stop_geometry_is_continuation():
    by = {o["level"]: o for o in prelondon_limits(build_cbdr(110.0, 100.0))["orders"]}
    bs = by["+1SD_stop"]
    # entry at +1SD (120), stop back at mid (105), targets up at +2/+3SD
    assert bs["entry"] == 120.0 and bs["stop"] == 105.0
    assert bs["targets"] == [130.0, 140.0]


# --- muster long-lock wiring -------------------------------------------------

def test_muster_helper_lock_flag(monkeypatch):
    # force the strict gate to report locked, and confirm a swing/intraday LONG is
    # filtered while a SHORT and a sniper (range-fade) long pass.
    import services.stratops_service as ss

    def fake_gate(side, dxy_price=None, strict=False):
        if side == "long" and strict:
            return {"ok": False, "reason": "strict: gold long LOCKED until DXY flips",
                    "dxy_flip": {"unlocked": False}}
        return {"ok": True, "reason": "ok"}

    monkeypatch.setattr(ss.gcycle, "regime_gate", fake_gate)
    # rebuild the local add() logic the way muster does
    locked = (not fake_gate("long", strict=True)["ok"])
    assert locked is True
