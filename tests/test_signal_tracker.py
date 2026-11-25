from services import signal_tracker as st


def _b(ts, h, l, c=None):
    return {"ts": ts, "high": h, "low": l, "close": c if c is not None else (h + l) / 2}


def test_tp1_then_breakeven_is_a_win_and_posts():
    s = st.new_signal("USDJPY", "SELL", 159.30, 159.467, [159.064, 158.746], "manual", ts=1000)
    ev = st.step(s, [_b(1100, 159.31, 159.20), _b(1400, 159.10, 159.05)], now=2000)   # TP1 hit
    assert [e["kind"] for e in ev] == ["tp"] and s["stop_now"] == 159.30
    ev = st.step(s, [_b(1700, 159.32, 159.10)], now=2000)                            # back to entry
    assert s["result"] == "win" and s["pips"] == 0.0 and ev[-1]["kind"] == "closed"


def test_stop_before_tp1_is_a_loss():
    s = st.new_signal("EURUSD", "SELL", 1.0919, 1.0929, [1.0908], "asia-london", ts=1000)
    st.step(s, [_b(1100, 1.0931, 1.0915)], now=2000)                # same bar: stop counts first
    assert s["result"] == "loss" and s["pips"] == -10.0


def test_pending_waits_for_fill_and_all_targets_close_win():
    s = st.new_signal("USDJPY", "SELL", 159.292, 159.467, [158.11], "manual", pending=True, ts=1000)
    st.step(s, [_b(1100, 159.20, 159.00)], now=2000)
    assert not s["filled"] and s["result"] == "open"
    st.step(s, [_b(1400, 159.30, 159.10), _b(1700, 159.0, 158.05)], now=2000)
    assert s["filled"] and s["result"] == "win" and s["pips"] == 118.2


def test_expiry_breakeven_and_summary():
    s = st.new_signal("EURUSD", "BUY", 1.0900, 1.0880, [1.0950], "z-setup", ts=0)
    st.step(s, [_b(100, 1.0905, 1.0895, 1.09005)], now=73 * 3600)
    assert s["result"] == "breakeven"
    sm = st.summarise([s, {**s, "result": "loss", "pips": -20.0}, {**s, "result": "win", "pips": 30.0}])
    assert (sm["wins"], sm["losses"], sm["breakeven"], sm["win_rate_pct"]) == (1, 1, 1, 50.0)


def test_targets_sorted_and_invalid_side_dropped():
    s = st.new_signal("USDJPY", "SELL", 159.3, 159.467, [158.11, 159.064, 160.0], "x")
    assert s["targets"] == [159.064, 158.11]


def test_retail_levels_parse_and_trapped_sides(tmp_path):
    import csv
    from services import retail_levels as rl
    rows = rl.parse("USDJPY\t\n155.7778\n-333 pips\t159.7094\n-61 pips\t159.095\t\n"
                    "EURCZK\t\n0\n-244068 pips\t24.4724\n-656 pips\t24.40680\t\n"
                    "XAUUSD\t\n3624.3459\n+5627 pips\t4124.8920\n-55681 pips\t3,568.08\t")
    assert [r["symbol"] for r in rows] == ["USDJPY", "XAUUSD"] and rows[1]["price"] == 3568.08
    p = tmp_path / "r.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "symbol", "short_avg", "short_pips", "long_avg", "long_pips", "price"])
        w.writeheader()
        for r in rows:
            w.writerow({"date": "2026-11-24", **r})
    lv = rl.levels("USD/JPY", str(p))
    assert [x["price"] for x in lv] == [159.7094, 155.7778]
    assert [x["price"] for x in rl.levels("XAU/USD", str(p))] == [4124.892]     # shorts are in profit


def test_votes_become_agent_cases_and_report():
    from services import council as cn
    w = st.new_signal("EURUSD", "SELL", 1.0919, 1.0929, [1.0908], "z-setup", ts=0, votes={"cot": 1, "vix": -1})
    st.step(w, [_b(100, 1.0915, 1.0905)], now=200)                       # TP1 → held
    l_ = st.new_signal("GBPUSD", "SELL", 1.3048, 1.3058, [1.3040], "asia-london", ts=0, votes={"cot": 1})
    st.step(l_, [_b(100, 1.3060, 1.3045)], now=200)                      # stop → broke
    sn = {**w, "source": "sniper"}
    cases = st.to_cases([w, l_, sn])
    assert [c["outcome"] for c in cases] == ["held", "broke"]
    sc = cn.agent_scores(cases)
    assert sc["cot"]["n"] == 2 and sc["cot"]["right"] == 1 and sc["vix"]["right"] == 0
    lines = st.report_lines(st.summarise([w, l_]))
    assert "1 · ❌ 1" in lines[0] and "z-setup 1W" in lines[1]


def test_partial_due_rules():
    s = st.new_signal("EURUSD", "SELL", 1.09637, 1.0975, [1.0939, 1.0920], "z-setup", ts=0)
    assert st.partial_due(s, -2.28, 1.09365)                  # sell in profit + oversold → bank partials
    assert not st.partial_due(s, +2.3, 1.09365)               # wrong extreme
    assert not st.partial_due(s, -2.3, 1.0970)                # not in profit
    assert not st.partial_due(s, -1.5, 1.09365)               # not stretched enough
    s["partial_alerted"] = True
    assert not st.partial_due(s, -2.5, 1.0930)                # only once


def test_flash_dedupe(monkeypatch):
    import asyncio
    from services import data_store, flash_card as fc
    store = {}

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    loop = asyncio.new_event_loop()
    assert loop.run_until_complete(fc._already_posted("EURUSD:SELL:1.0964:1.0939")) is False
    assert loop.run_until_complete(fc._already_posted("EURUSD:SELL:1.0964:1.0939")) is True


def test_same_trade_from_two_engines_is_one_signal(monkeypatch):
    import asyncio
    rows = []

    async def load():
        return rows

    async def save(r):
        rows[:] = r
    monkeypatch.setattr(st, "_load", load)
    monkeypatch.setattr(st, "_save", save)
    import services.snapshots as sn

    async def cap(s):
        return True
    monkeypatch.setattr(sn, "capture", cap)
    loop = asyncio.new_event_loop()
    a = loop.run_until_complete(st.record("EUR/USD", "SELL", 1.09637, 1.0975, [1.0939], "sniper"))
    b = loop.run_until_complete(st.record("EUR/USD", "SELL", 1.09640, 1.0976, [1.0939], "z-setup"))
    assert len(rows) == 1 and b["id"] == a["id"] and rows[0]["source"] == "sniper+z-setup"
