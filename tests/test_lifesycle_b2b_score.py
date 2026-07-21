"""Batch 3: position lifecycle alert narration + b2b into STRATOPS scoring."""

from gold.position import format_lifecycle_events
from gold import stratops


# --- lifecycle narration -----------------------------------------------------

def test_lifecycle_narrates_each_transition():
    events = [
        {"kind": "filled", "side": "long", "trade_type": "sniper", "entry": 3268.0, "stop": 3260.0},
        {"kind": "tp", "side": "long", "trade_type": "sniper", "tp_hit": 1, "running_r": 1.5},
        {"kind": "breakeven", "side": "long", "trade_type": "sniper", "stop": 3268.0, "running_r": 1.5},
        {"kind": "closed", "side": "long", "trade_type": "sniper", "exit_reason": "TP2",
         "exit_price": 3300.0, "result_r": 4.0},
    ]
    txt = format_lifecycle_events(events)
    assert "FILLED" in txt and "TP1" in txt and "STOP→BE" in txt and "CLOSED" in txt
    assert "+1.5R" in txt and "+4.0R" in txt or "+4R" in txt


def test_lifecycle_empty_is_none():
    assert format_lifecycle_events([]) is None


# --- b2b confluence into the score ------------------------------------------

def _cand(tt="sniper", b2b=None):
    c = {"signal": "LONG", "trade_type": tt, "entry": 3300, "risk_usd": 20,
         "gate": {"allow": True}, "campaign": {"status": "advances"},
         "htf_confluence": "aligns", "regime": {"status": "confirms"},
         "location": {"ok": True}, "protraction": {"direction": "long"},
         "liquidity_draw": {"price": 3500}, "targets": [{"rr": 8}]}
    if b2b is not None:
        c["b2b_confluence"] = b2b
    return c


def test_b2b_bonus_lifts_the_score():
    # a b2b-confirmed candidate scores at least as high (and its parts show the bonus)
    base = stratops.score_candidate(_cand(tt="swing", b2b=False))
    conf = stratops.score_candidate(_cand(tt="swing", b2b=True))
    assert conf["parts"]["b2b"] == stratops.B2B_BONUS
    assert base["parts"]["b2b"] == 0
    assert conf["score"] >= base["score"]
    assert conf["b2b"] is True


def test_b2b_breaks_rank_ties():
    # two identical candidates, one with b2b — the b2b one ranks first
    a = _cand(tt="crt", b2b=False)     # crt/swing factor 1.0, base < 100 so bonus shows
    b = _cand(tt="crt", b2b=True)
    ranked = stratops.rank([a, b])
    assert ranked[0]["stratops"]["b2b"] is True
