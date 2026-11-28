import datetime as dt

from services import thesis

TH = thesis.load()["XAUUSD"]


def _b(*rows):
    return [{"d": f"2026-12-{i + 1:02d}", "high": h, "low": lo, "close": c} for i, (h, lo, c) in enumerate(rows)]


def test_seed_thesis_has_levels_and_both_options():
    assert TH["levels"] == {"floor": 3487, "pivot_low": 4084, "pivot_high": 4200, "line": 4340}
    assert {s["side"] for s in TH["scenarios"]} == {"BUY", "SELL"}


def test_legs_follow_price():
    assert thesis.leg(TH, _b((3700, 3600, 3650)), 3660)["leg"] == "bounce"
    assert thesis.leg(TH, _b((3700, 3600, 3650)), 4150)["leg"] == "decision"
    assert thesis.leg(TH, _b((4120, 3900, 4000), (4000, 3850, 3880)), 3870)["leg"] == "rejected"
    assert thesis.leg(TH, _b((4120, 3900, 4000), (3600, 3450, 3460)), 3450)["leg"] == "final_leg"
    assert thesis.leg(TH, _b((3600, 3400, 3450)), 3440)["leg"] == "early_break"
    assert thesis.leg(TH, _b((4400, 4100, 4380)), 4390)["leg"] == "opt1_confirmed"


def test_bias_combines_thesis_edge_and_spotter():
    lg = thesis.leg(TH, _b((3700, 3600, 3650)), 3660)                 # bounce → short-term BUY
    strong = thesis.combine(lg, {"score": 5}, {"tilt": 0.5, "signals": 2})
    assert strong["bias"] == "BUY" and strong["conviction"] == "strong" and strong["score"] == 4
    mixed = thesis.combine(lg, {"score": -6}, {"tilt": -0.6, "signals": 2})
    assert mixed["bias"] == "NEUTRAL" and mixed["conviction"] == "mixed"


def test_evaluate_marks_scenarios_and_event():
    r = thesis.evaluate("XAUUSD", TH, _b((4120, 3900, 4000), (3600, 3450, 3460)), 3450,
                        today=dt.date(2026, 12, 8))
    st = {s["key"]: s["state"] for s in r["scenarios"]}
    assert st == {"opt2": "confirmed", "opt1": "invalidated"}
    assert next(s for s in r["scenarios"] if s["key"] == "opt2")["next_target"] == 3021
    assert "FOMC in 1 day" in r["event"]
    t = thesis.text(r)
    assert "Best bias now: *SELL*" in t and "council GO" in t
