from services import council as cn


def test_agents_vote_and_decide():
    votes = [cn.vote_cot({"verdict": "LONG", "confidence": 90, "cot_agrees": True}, "BUY"),
             cn.vote_sentiment({"composite": -0.4}, "BUY"),
             cn.vote_weekly({"bias": "long", "profile": "prior_week_x"}, "BUY"),
             cn.vote_structure({"ok": True, "ote": {"side": "BUY", "leg": "bull"},
                                "magnet": {"year": 2020, "side": "below"}}, "BUY"),
             cn.vote_history({"role": "TP1", "n": 5, "held": 4, "recent_rate": 0.7}),
             cn.vote_macro(None, "BUY"), cn.vote_mirofish(None, "BUY")]
    assert [v["vote"] for v in votes] == [1, -1, 1, 1, 1, 0, 0]
    d = cn.decide(votes)
    # 4 for but 1 against (sentiment) → HOLD under the owner's 4-for / 0-against rule
    assert d["verdict"] == "HOLD" and d["score"] == 3 and d["for"] == 4 and d["against"] == 1
    assert "Council *HOLD*" in cn.summary(d)


def test_macro_lock_vetoes():
    d = cn.decide([cn.vote_cot({"verdict": "LONG", "confidence": 95}, "BUY"),
                   cn.vote_macro("SELL_ONLY", "BUY")])
    assert d["verdict"] == "VETO" and d["veto"] == ["macro"]


def test_mirofish_bands():
    assert cn.vote_mirofish({"score": 70}, "BUY")["vote"] == 1
    assert cn.vote_mirofish({"score": 50}, "BUY")["vote"] == 0
    assert cn.vote_mirofish({"score": 20}, "BUY")["vote"] == -1


def test_four_for_zero_against_rule():
    up = lambda a: {"agent": a, "vote": 1, "why": ""}          # noqa: E731
    down = lambda a: {"agent": a, "vote": -1, "why": ""}       # noqa: E731
    assert cn.decide([up("a"), up("b"), up("c"), up("d")])["verdict"] == "GO"
    held = cn.decide([up("a"), up("b"), up("c"), up("d"), down("e")])
    assert held["verdict"] == "HOLD" and "held until promoted" in held["need"]
    assert cn.decide([up("a"), up("b"), up("c")])["verdict"] == "HOLD"
