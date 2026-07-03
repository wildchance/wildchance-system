from edgefinder.engine import score_pair, label, agreement, _retail_factor, _cot_factor


def test_agreement_confirms_diverges_neutral():
    assert agreement(4, "long") == "confirms"
    assert agreement(4, "short") == "diverges"
    assert agreement(-3, "sell") == "confirms"
    assert agreement(-3, "buy") == "diverges"
    assert agreement(0, "long") == "neutral"      # no edge → don't block
    assert agreement(2, "flat") == "neutral"


def _sig(**kw):
    base = {"pair": "EUR/USD", "price": 1.1, "retail_long": 50, "retail_extreme": 50,
            "contrarian": "long", "cot_net": None, "cot_rank": None,
            "cot_agrees": None, "verdict": "FLAT", "confidence": 40}
    base.update(kw)
    return base


def test_label_thresholds():
    assert label(3) == "STRONG LONG"
    assert label(1) == "LONG"
    assert label(0) == "NEUTRAL"
    assert label(-1) == "SHORT"
    assert label(-4) == "STRONG SHORT"


def test_retail_balanced_scores_zero():
    assert _retail_factor(_sig(retail_extreme=52))["score"] == 0


def test_retail_crowded_long_votes_contrarian_short_double():
    f = _retail_factor(_sig(retail_long=72, retail_extreme=72, contrarian="short"))
    assert f["score"] == -2          # extreme ≥65 → ×2


def test_cot_agrees_with_contrarian_extreme_rank():
    # contrarian short + COT agrees → COT short; rank 0.15 extreme → ×2
    f = _cot_factor(_sig(contrarian="short", cot_agrees=True,
                         cot_net=-40000, cot_rank=0.15))
    assert f["score"] == -2


def test_cot_disagrees_flips_direction():
    # contrarian long + COT disagrees → COT short; rank mid → ×1
    f = _cot_factor(_sig(contrarian="long", cot_agrees=False,
                         cot_net=10000, cot_rank=0.5))
    assert f["score"] == -1


def test_strong_short_when_retail_and_cot_align():
    sig = _sig(retail_long=72, retail_extreme=72, contrarian="short",
               cot_agrees=True, cot_net=-40000, cot_rank=0.15, verdict="SHORT")
    row = score_pair(sig)
    assert row["score"] == -4 and row["bias"] == "STRONG SHORT"


def test_retail_and_cot_conflict_nets_neutral():
    sig = _sig(retail_long=40, retail_extreme=60, contrarian="long",
               cot_agrees=False, cot_net=5000, cot_rank=0.5)
    row = score_pair(sig)
    assert row["score"] == 0 and row["bias"] == "NEUTRAL"


def test_mmm_factor_adds_when_provided():
    sig = _sig(retail_long=68, retail_extreme=68, contrarian="short",
               cot_agrees=True, cot_net=-30000, cot_rank=0.3)   # retail -2, cot -1
    row = score_pair(sig, mmm_bias={"bias": "short", "score": -2})
    assert any(f["name"] == "mmm" for f in row["factors"])
    assert row["score"] == -5 and row["bias"] == "STRONG SHORT"


def test_news_passthrough():
    row = score_pair(_sig(), news="⚠️ USD NFP 2026-07-03")
    assert row["news"] == "⚠️ USD NFP 2026-07-03"
