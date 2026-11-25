from services import deal_tracker as dt_

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>ZOTAC RTX 4090 Trinity 24GB $1,399.99 + Free Shipping</title><link>https://x/1</link></item>
<item><title>Hetzner dedicated server deal $39/mo</title><link>https://x/2</link></item>
<item><title>Coffee maker $19</title><link>https://x/3</link></item></channel></rss>"""


def test_parse_and_match():
    items = dt_.parse_rss(RSS)
    assert items[0]["price"] == 1399.99 and items[1]["price"] == 39.0
    w = dt_.parse_watch(["rtx", "4090", "1500"])
    assert w == {"words": ["rtx", "4090"], "max": 1500.0}
    assert dt_.matches(items[0], w) and not dt_.matches(items[2], w)
    assert not dt_.matches(items[0], dt_.parse_watch(["rtx", "4090", "1000"]))
    assert dt_.matches(items[1], dt_.parse_watch(["server"]))
    assert dt_.parse_rss("not xml") == [] and dt_.parse_watch([]) is None
