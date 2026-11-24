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
