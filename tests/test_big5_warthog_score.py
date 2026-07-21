"""Big-5 pip tiers, 250-pip minimum-capture hold exit, warthog into scoring."""

import pytest

from gold import big5
from gold.position import prelondon_exit
from gold import stratops
from cbdr.engine import build_cbdr


# --- Big-5 tiers -------------------------------------------------------------

def test_big5_tiers_named():
    assert big5.tier_for_pips(250)["name"] == "cheetah"
    assert big5.tier_for_pips(500)["name"] == "leopard"
    assert big5.tier_for_pips(750)["name"] == "lion"
    assert big5.tier_for_pips(1000)["name"] == "buffalo"
    assert big5.tier_for_pips(1250)["name"] == "rhino"
    assert big5.tier_for_pips(1500)["name"] == "elephant"
    assert big5.tier_for_pips(200) is None            # below cheetah


def test_classify_capture_leopard():
    # 4000 → 4050 long = 50.0 move / 0.10 pip = 500 pips = leopard
    c = big5.classify_capture(4000.0, 4050.0, "long")
    assert c["pips"] == 500.0 and c["tier"]["name"] == "leopard"
    assert c["meets_min"] is True


def test_min_capture_floor_is_cheetah_250():
    assert big5.MIN_CAPTURE_PIPS == 250


# --- 250-pip minimum-capture on the hold exit --------------------------------

def test_hold_holds_when_level_banks_under_cheetah():
    # entry 4030, +1SD=4040 (100 pips), +1.5SD=4050 (200 pips) — both < 250 → HOLD
    lv = build_cbdr(4020.0, 4000.0).levels
    st = {"side": "long", "entry": 4030.0, "stop_initial": 4016.0}
    assert prelondon_exit(st, lv, 4055.0) is None     # price past +1.5SD but < cheetah


def test_hold_releases_at_cheetah_or_more():
    # entry 4004, +1.5SD=4050 = 460 pips ≥ 250 → exit, tagged cheetah
    lv = build_cbdr(4020.0, 4000.0).levels
    st = {"side": "long", "entry": 4004.0, "stop_initial": 3990.0}
    ex = prelondon_exit(st, lv, 4055.0)
    assert ex is not None and ex["exit_price"] == 4050.0
    assert ex["capture_pips"] >= 250 and ex["tier"]["name"] == "cheetah"
    assert "cheetah" in ex["exit_reason"]


def test_hold_short_side_min_capture():
    lv = build_cbdr(4020.0, 4000.0).levels            # -1SD=3980, -1.5SD=3970
    st = {"side": "short", "entry": 4016.0, "stop_initial": 4030.0}
    ex = prelondon_exit(st, lv, 3968.0)               # 460 pips
    assert ex["capture_pips"] >= 250 and ex["exit_price"] == 3970.0


# --- warthog into STRATOPS scoring -------------------------------------------

def _cand(b2b=None, wh=None, tt="swing"):
    c = {"signal": "LONG", "trade_type": tt, "entry": 3300, "risk_usd": 20,
         "gate": {"allow": True}, "campaign": {"status": "advances"},
         "htf_confluence": "aligns", "regime": {"status": "confirms"},
         "location": {"ok": True}, "protraction": {"direction": "long"},
         "liquidity_draw": {"price": 3500}, "targets": [{"rr": 8}]}
    if b2b is not None:
        c["b2b_confluence"] = b2b
    if wh is not None:
        c["warthog_confluence"] = wh
    return c


def test_warthog_bonus_in_score():
    base = stratops.score_candidate(_cand(wh=False))
    conf = stratops.score_candidate(_cand(wh=True))
    assert conf["parts"]["warthog"] == stratops.WARTHOG_BONUS
    assert base["parts"]["warthog"] == 0
    assert conf["warthog"] is True


def test_b2b_and_warthog_stack_the_tiebreak():
    plain = _cand(tt="crt")
    both = _cand(b2b=True, wh=True, tt="crt")
    ranked = stratops.rank([plain, both])
    assert ranked[0]["stratops"]["b2b"] and ranked[0]["stratops"]["warthog"]
