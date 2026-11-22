"""WildChance v4: lunar overlay, prop-challenge guard, 4-step pipeline, VPS guard."""

import datetime as dt

from lunar import engine as lu
from pipeline import engine as pe
from propfirm import challenge as ch
from mt5_bridge.equity_guard import EquityGuard

UTC = dt.timezone.utc


# --- lunar ---------------------------------------------------------------------------
def test_true_phase_matches_reference_new_moons():
    # k=0: 2000-01-06 18:14 UTC; Meeus example 49.a: 1977-02-18 03:37:42 TD (k=-283)
    assert abs((lu.true_phase(0) - dt.datetime(2000, 1, 6, 18, 14, tzinfo=UTC)).total_seconds()) < 120
    assert abs((lu.true_phase(-283) - dt.datetime(1977, 2, 18, 3, 37, tzinfo=UTC)).total_seconds()) < 180


def test_lunar_state_waxing_is_buy_and_waning_is_sell():
    nm = lu.true_phase(330)
    fm = lu.true_phase(330.5)
    wax = lu.lunar_state(nm + dt.timedelta(days=5))
    wan = lu.lunar_state(fm + dt.timedelta(days=5))
    assert wax["allowed_direction"] == "BUY" and not wax["in_observation_window"]
    assert wan["allowed_direction"] == "SELL" and wan["max_hold_days"] == 12


def test_observation_window_halves_risk():
    st = lu.lunar_state(lu.true_phase(330.5) + dt.timedelta(hours=10), observation_hours=24)
    assert st["in_observation_window"] and st["risk_multiplier"] == 0.5
    assert st["phase"] == "FULL_MOON_OBSERVATION"


def test_alignment_blocks_mismatch_and_pause():
    t = lu.true_phase(330) + dt.timedelta(days=5)
    assert lu.evaluate_alignment("long", t)["allowed"]
    assert not lu.evaluate_alignment("SELL", t)["allowed"]
    t_obs = lu.true_phase(330) + dt.timedelta(hours=3)
    assert not lu.evaluate_alignment("BUY", t_obs, pause_during_observation=True)["allowed"]


def test_phase_returns_reports_no_evidence_on_random_walk():
    import random
    random.seed(7)
    d0, px, bars = dt.date(2018, 1, 1), 1300.0, []
    for i in range(2000):
        px *= 1 + random.gauss(0, 0.009)
        bars.append((d0 + dt.timedelta(days=i), px, px, px, px))
    out = lu.phase_returns(bars, 14)
    assert out["ok"] and out["new_moon_longs"]["n"] > 50
    assert out["verdict"].startswith("NO EVIDENCE")


# --- prop challenge ------------------------------------------------------------------
def test_guard_lines_use_buffers_inside_firm_limits():
    g = ch.equity_guard(10000, 10000, 10000, 10000)
    assert g["guard_daily_floor"] == 9650.0 and g["firm_daily_floor"] == 9500.0
    assert g["guard_max_floor"] == 9200.0 and g["action"] == "TRADE"


def test_guard_actions():
    assert ch.equity_guard(9640, 10000, 10000, 10000)["action"] == "DAILY_LOCK"
    assert ch.equity_guard(9190, 10000, 9300, 9300)["action"] == "MAX_LOCK"
    assert ch.equity_guard(10800, 10000, 10700, 10700)["action"] == "TARGET_LOCK"
    # FundingPips day reference = max(balance, equity) at day start
    assert ch.equity_guard(10100, 10000, 10000, 10500)["action"] == "DAILY_LOCK"


def test_challenge_plan_math_and_warnings():
    p = ch.challenge_plan(10000, risk_pct=0.5, win_rate=0.45, avg_r=1.8, max_concurrent=3)
    assert p["losers_to_daily_guard"] == 7 and p["losers_to_max_guard"] == 16
    assert p["phases"][0]["net_r_needed"] == 16.0 and p["expectancy_r"] > 0
    bad = ch.challenge_plan(10000, risk_pct=1.5, win_rate=0.3, avg_r=1.5, max_concurrent=4)
    assert len(bad["warnings"]) == 3


def test_size_lots_never_rounds_up_into_risk():
    # 0.5% of 10k = $50; SL 20 price units, $100 per unit per lot → 0.025 → 0.02
    assert ch.size_lots(10000, 0.5, 20, 100) == 0.02
    assert ch.size_lots(1000, 0.5, 50, 100) is None        # min lot would over-risk


# --- VPS equity guard ----------------------------------------------------------------
def test_equity_guard_persists_initial_balance_across_restart(tmp_path):
    f = str(tmp_path / "g.json")
    g = EquityGuard(initial_balance=None, state_file=f)
    assert g.check(10000, 10000, "2026-11-23")["action"] == "TRADE"
    # restart after a losing streak: initial balance must NOT reset to 9300
    g2 = EquityGuard(initial_balance=None, state_file=f)
    v = g2.check(9300, 9300, "2026-11-24")
    assert v["initial_balance"] == 10000 and v["max_floor"] == 9200


def test_equity_guard_daily_lock_holds_until_next_day(tmp_path):
    g = EquityGuard(initial_balance=10000, state_file=str(tmp_path / "g.json"), target=0)
    g.check(10000, 10000, "D1")
    assert g.check(10000, 9640, "D1")["action"] == "DAILY_LOCK"
    assert g.check(10000, 9900, "D1")["action"] == "DAILY_LOCK"     # recovery ≠ unlock
    assert g.check(9900, 9900, "D2")["action"] == "TRADE"
    assert g.check(9100, 9100, "D3")["action"] == "MAX_LOCK"
    assert g.check(9900, 9900, "D4")["action"] == "MAX_LOCK"        # permanent


# --- 4-step pipeline -----------------------------------------------------------------
def _wl():
    base = [("XAUUSD", "BUY", .50, 2.2, 31), ("EURUSD", "SELL", .48, 2.0, 26),
            ("GBPJPY", "BUY", .47, 1.9, 24), ("US500", "SELL", .46, 2.4, 28),
            ("AUDNZD", "SELL", .55, 1.5, 14), ("USDCAD", "BUY", .44, 1.9, 22),
            ("NAS100", "SELL", .45, 2.0, 25), ("USOIL", "BUY", .43, 1.8, 21),
            ("EURGBP", "BUY", .52, 1.2, 12), ("USDJPY", "BUY", .30, 1.0, 18)]
    return [{"symbol": s, "side": d, "win_rate": p, "rr": rr, "adx": a, "cost_r": .05}
            for s, d, p, rr, a in base]


def test_chop_traps_rank_below_trend_and_outliers_cut():
    out = pe.run_audit(_wl())
    ranked = out["ranked"]
    assert ranked[0]["rank"] == "R01" and ranked[0]["class"] == "ANCHOR"
    chop_idx = [i for i, r in enumerate(ranked) if r["chop_trap"]]
    trend_idx = [i for i, r in enumerate(ranked) if not r["chop_trap"]]
    assert min(chop_idx) > max(trend_idx)
    assert sum(r["class"] == "OUTLIER" for r in ranked) >= 2
    assert all(r["action"] == "CUT" for r in ranked if r["class"] == "OUTLIER")


def test_compact_ladder_concurrency_cap_and_clusters():
    out = pe.run_audit(_wl(), risk_pct=0.5, max_concurrent=3)
    assert out["mode"] == "compact"
    assert sum(r["action"] == "EXECUTE" for r in out["ranked"]) == 3
    assert any(x["tier"].startswith("+1 RUNNER") for x in out["ladder"])
    usd = [r for r in out["ranked"] if r["symbol"] == "US500"][0]
    assert "[CLUSTER-RISK]" in usd["flags"] and usd["risk_pct"] <= 0.25


def test_news_setups_paused_in_prop_mode():
    wl = _wl()
    wl[0]["news"] = ["US CPI m/m"]
    out = pe.run_audit(wl)
    xau = [r for r in out["ranked"] if r["symbol"] == "XAUUSD"][0]
    assert "[NEWS]" in xau["flags"] and xau["action"].startswith("PAUSE")


def test_macro_lock_and_lunar_mismatch():
    lunar = {"allowed_direction": "BUY", "in_observation_window": False}
    out = pe.run_audit(_wl(), use_lunar=True, lunar=lunar, macro_bias="BUY_ONLY")
    eur = [r for r in out["ranked"] if r["symbol"] == "EURUSD"][0]
    assert "[MACRO-LOCK]" in eur["flags"] and eur["action"] == "SKIP"


def test_sigma_ladder_targets():
    t = pe._ladder(3540.0, 3505.0, 75.0, 1, runner=True)
    assert t["tp_core_1sigma"] == 3615.0 and t["tp_runner_3sigma"] == 3765.0
    assert t["tp1_1R"] == 3575.0 and t["core_rr"] == 2.14


def test_calibration_brier():
    c = pe.calibration([{"prob": .7, "won": True, "r": 1.8}, {"prob": .7, "won": False, "r": -1}])
    assert c["brier"] == 0.29 and c["win_rate"] == 0.5 and c["overconfident"]


# --- API ---------------------------------------------------------------------------
def test_v4_routes_mounted():
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    assert c.get("/lunar/state").json()["allowed_direction"] in ("BUY", "SELL")
    r = c.post("/pipeline/audit", json={"watchlist": _wl(), "use_lunar": True}).json()
    assert r["setups"] == 10 and "lunar" in r
    assert c.get("/propfirm/challenge/plan?balance=5000").json()["risk_usd"] == 25.0
    g = c.get("/propfirm/challenge/guard?equity=9640&initial_balance=10000&day_start_balance=10000")
    assert g.json()["action"] == "DAILY_LOCK"


# --- data feed fallback --------------------------------------------------------------
def test_yahoo_symbol_mapping_and_parser():
    from services import ohlc_service as oh
    assert oh.yahoo_symbol("EURUSD") == "EURUSD=X"
    assert oh.yahoo_symbol("XAUUSD") == "GC=F"
    data = {"chart": {"result": [{"timestamp": [1763337600, 1763424000],
            "indicators": {"quote": [{"open": [1, None], "high": [2, 3],
                                      "low": [0.5, 1], "close": [1.5, 2]}]}}]}}
    bars = oh.parse_yahoo_chart(data)
    assert len(bars) == 1 and bars[0][4] == 1.5
    assert oh.parse_yahoo_chart({}) == []


def test_twelvedata_error_is_logged(caplog):
    import logging
    from services import ohlc_service as oh
    oh._last_td_warn[0] = 0
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        oh._td_error({"status": "error", "code": 429, "message": "run out of API credits"})
    assert "run out of API credits" in caplog.text


# --- FundingPips presets = the official plan pages (user screenshots 2026-11-22) -----
def test_fundingpips_presets_match_official_plans():
    P = ch.PRESETS
    def chk(k, targets, daily, mx, ref, days):
        p = P[k]
        assert (p["targets"], p["daily_loss"], p["max_loss"], p["max_ref"], p["min_days"]) == \
            (targets, daily, mx, ref, days), k
    chk("fundingpips_2step_8", [0.08, 0.05], 0.05, 0.10, "static", 3)
    chk("fundingpips_2step_8_d3", [0.08, 0.05], 0.03, 0.10, "static", 3)
    chk("fundingpips_2step_flex", [0.10, 0.08], 0.04, 0.12, "static", 1)
    chk("fundingpips_2step_pro", [0.06, 0.06], 0.03, 0.06, "static", 0)
    chk("fundingpips_1step", [0.12], 0.03, 0.12, "static", 0)
    chk("fundingpips_1step_d2", [0.12], 0.02, 0.12, "static", 0)
    chk("fundingpips_1step_labs", [0.05], 0.01, 0.03, "eod", 0)
    chk("fundingpips_zero", [], 0.03, 0.05, "trailing", 7)
    assert "fundingpips_2step_10" not in P          # not a real plan
    from mt5_bridge.equity_guard import PRESETS as BP
    for k, (d, m, ref, t) in BP.items():            # bridge mirror == app table
        assert (P[k]["daily_loss"], P[k]["max_loss"], P[k]["max_ref"], P[k]["targets"]) == (d, m, ref, t), k


def test_labs_eod_guard_and_tight_risk_warning():
    g = ch.equity_guard(10200, 10000, 10300, 10300, phase=1,
                        preset="fundingpips_1step_labs", peak_equity=10300)
    # EOD high 10300 − 3%×10000×0.8 = 10060 ; daily 10300 − 1%×10000×0.7 = 10230 → daily lock
    assert g["guard_max_floor"] == 10060.0 and g["action"] == "DAILY_LOCK"
    plan = ch.challenge_plan(10000, "fundingpips_1step_labs", risk_pct=0.5)
    assert plan["losers_to_daily_guard"] == 1 and plan["warnings"]


def test_bridge_eod_and_trailing(tmp_path):
    from mt5_bridge.equity_guard import EquityGuard
    g = EquityGuard(initial_balance=10000, daily_loss=0.01, max_loss=0.03, target=0,
                    trailing_max="eod", state_file=str(tmp_path / "e.json"))
    g.check(10000, 10000, "D1")
    g.check(10500, 10500, "D2")                      # EOD high 10500 → floor 10260
    assert g.check(10500, 10250, "D2")["max_floor"] == 10260.0
    z = EquityGuard(initial_balance=10000, daily_loss=0.03, max_loss=0.05, target=0,
                    trailing_max=True, state_file=str(tmp_path / "z.json"))
    z.check(10000, 10800, "D1")                      # intraday peak counts for trailing
    assert z.check(10000, 10500, "D1")["max_floor"] == 10400.0


def test_lunar_gate_blocks_counter_phase_signal(monkeypatch):
    import services.trade_executor as te
    from lunar import engine as lu
    monkeypatch.setattr(te, "LUNAR_GATE_ENABLED", True)
    waxing = lu.true_phase(330) + dt.timedelta(days=5)
    real = lu.evaluate_alignment
    monkeypatch.setattr(lu, "evaluate_alignment",
                        lambda d, dt=None, **kw: real(d, waxing, **kw))
    assert te.lunar_gate({"signal": "LONG"})["approved"]
    v = te.lunar_gate({"signal": "SHORT"})
    assert not v["approved"] and "MISMATCH" in v["reason"]
    assert te.lunar_gate({"signal": None})["approved"]          # no side → fail-open
    monkeypatch.setattr(te, "LUNAR_GATE_ENABLED", False)
    assert te.lunar_gate({"signal": "SHORT"})["approved"]
