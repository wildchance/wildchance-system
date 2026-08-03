"""Free macro feeds → new VAULTUM scores (JPY carry, geopolitical, CB divergence)."""

from gold import vaultum_scores as vs
from services import free_macro_feeds as fm


# --- new score envelopes ----------------------------------------------------------

def test_jpy_carry_unwind_is_bullish_gold():
    unwind = vs.jpy_liquidity_score(-4.0)     # yen strengthening → risk-off
    carry = vs.jpy_liquidity_score(+4.0)      # yen weak → risk-on
    assert unwind["value"] > 50 > carry["value"]
    assert unwind["value"] > carry["value"]


def test_geopolitical_high_risk_supports_gold():
    hi = vs.geopolitical_score(90)
    lo = vs.geopolitical_score(10)
    assert hi["value"] > 50 > lo["value"]


def test_cb_divergence_fed_hawkish_bearish_gold():
    hawk = vs.cb_divergence_score(2.5)        # Fed well above peers
    dove = vs.cb_divergence_score(-2.5)
    assert hawk["value"] < 50 < dove["value"]


def test_new_scores_degrade_to_neutral():
    for s in (vs.jpy_liquidity_score(None), vs.geopolitical_score(None),
              vs.cb_divergence_score(None)):
        assert s["value"] == 50.0 and s["confidence"] <= 0.2


# --- geopolitical news-headline feed (reliable primary, GDELT fallback) ------------

_RSS = """<rss><channel><title>Google News</title>
<item><title>Missile strike kills dozens as invasion escalates</title></item>
<item><title>Air strike on port, troops advance in new offensive</item></title></item>
<item><title>Nuclear tensions rise after retaliation threat</title></item>
<item><title>Markets rally on tech earnings</title></item>
<item><title>Central bank holds rates steady</title></item>
<item><title>New sanctions announced amid conflict</title></item>
</channel></rss>"""


def test_rss_titles_skip_channel_and_unescape():
    titles = fm._parse_rss_titles(_RSS)
    assert "google news" not in titles           # channel title skipped
    assert any("missile strike" in t for t in titles)


def test_geo_headline_score_high_when_escalation_dense():
    titles = fm._parse_rss_titles(_RSS)
    hi = fm._score_geo_headlines(titles)
    assert hi is not None and hi > 60           # lots of strike/missile/invasion words
    calm = ["markets rise", "earnings beat", "rates held", "trade talks resume",
            "growth steady", "jobs report solid"]
    assert fm._score_geo_headlines(calm) == 40.0   # baseline when no escalation words


def test_geo_headline_score_needs_minimum_headlines():
    assert fm._score_geo_headlines(["war", "missile"]) is None   # too few → defer to fallback


# --- cb_divergence math (encoded rates) -------------------------------------------

def test_cb_divergence_uses_policy_rates():
    d = fm.cb_divergence()
    assert "fed_minus_peers" in d and d["fed"] == fm.POLICY_RATES["FED"]
    # Fed above the peer average by construction of the default map
    assert d["fed_minus_peers"] == round(d["fed"] - d["peer_avg"], 2)


def test_set_policy_rates_updates():
    before = fm.POLICY_RATES["FED"]
    fm.set_policy_rates(fed=3.75)
    assert fm.POLICY_RATES["FED"] == 3.75
    fm.set_policy_rates(fed=before)           # restore


# --- board includes the new directional scores -----------------------------------

def test_board_weights_include_new_signals():
    assert "jpy_liquidity" in vs._DIRECTIONAL
    assert "geopolitical" in vs._DIRECTIONAL
    assert "cb_divergence" in vs._DIRECTIONAL
    assert "volume_location" in vs._DIRECTIONAL
    # weights still sum sensibly (all positive, <= 1)
    assert 0.9 <= sum(vs._DIRECTIONAL.values()) <= 1.0
