"""Five-account fleet + copy-trade fan-out."""

import pytest

from gold import accounts as ga


# --- acc4: 10x compounding to 167,350 ---------------------------------------

def test_compound_10x_hits_167350():
    out = ga.compound_10x(700, runs=4)
    assert out["final_balance"] == pytest.approx(167350.0, abs=1.0)
    lots = [r["lot"] for r in out["ladder"]]
    assert lots == [0.01, 0.10, 1.00, 10.00]
    assert out["ladder"][-1]["gain"] == pytest.approx(150000.0)


# --- acc5: 2500-pip trend layering ------------------------------------------

def test_trend_layer_2500():
    p = ga.trend_layer_plan(4000.0, "long", range_pips=2500, layers=6)
    # long target = anchor + 2500 pips ($250)
    assert p["target"] == pytest.approx(4250.0)
    assert len(p["orders"]) == 6
    # retracement layers step DOWN into discount for a long
    entries = [o["entry"] for o in p["orders"]]
    assert entries == sorted(entries, reverse=True)
    # deeper layers carry a bigger lot (scale-in)
    assert p["orders"][-1]["lot"] > p["orders"][0]["lot"]


# --- fleet registry ----------------------------------------------------------

def test_fleet_has_five_accounts():
    assert set(ga.FLEET) == {"acc1", "acc2", "acc3", "acc4", "acc5"}
    assert ga.FLEET["acc1"]["strategy"] == "cent_flipper"
    assert ga.FLEET["acc4"]["strategy"] == "compound_10x"


def test_account_plan_dispatch():
    assert ga.account_plan("acc1", denom="cent")["strategy"] == "cent_flipper"
    assert ga.account_plan("acc4")["final_balance"] == pytest.approx(167350.0, abs=1.0)
    assert "phases" in ga.account_plan("acc2")
    assert ga.account_plan("acc5", anchor=4000.0)["strategy"] == "trend_layer_2500"
    assert "error" in ga.account_plan("acc9")


def test_fleet_plan_all_denoms():
    for d in ga.DENOMINATIONS:
        fp = ga.fleet_plan(denom=d)
        assert set(fp["accounts"]) == set(ga.FLEET)


# --- copy-trade fan-out ------------------------------------------------------

def test_copy_fanout_sizes_per_account():
    sig = {"side": "short", "entry": 4059.0, "stop": 4067.0,
           "targets": [{"price": 4044.0}]}
    accts = [
        {"id": "acc1", "balance": 700, "denom": "cent", "risk_pct": 1.0},
        {"id": "acc2", "balance": 5000, "denom": "USD", "risk_pct": 1.0},
        {"id": "acc5", "balance": 100000, "denom": "KES", "risk_pct": 1.0},
    ]
    out = ga.copy_fanout(sig, accts)
    assert out["ok"] is True and len(out["fanout"]) == 3
    # same entry/stop everywhere, bigger balance → bigger lot
    lots = {r["account"]: r["lot"] for r in out["fanout"]}
    assert lots["acc5"] > lots["acc2"] > lots["acc1"]
    assert all(r["entry"] == 4059.0 and r["side"] == "short" for r in out["fanout"])


def test_copy_fanout_needs_entry_stop():
    assert ga.copy_fanout({"side": "long"}, [])["ok"] is False
