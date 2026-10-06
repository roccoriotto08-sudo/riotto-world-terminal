from backend.app.news import Tagger, parse_date, parse_feed

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title><![CDATA[Oil jumps as Houthi missiles hit tanker in Red Sea]]></title><link>https://ex.com/a</link>
<pubDate>Mon, 05 Oct 2026 21:46:22 GMT</pubDate><description>full text we must never store</description></item>
<item><title>No date item</title><link>https://ex.com/b</link></item>
</channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Nvidia shares rally (NVDA)</title>
<link href="https://ex.com/c"/><updated>2026-10-05T13:56:58Z</updated></entry></feed>"""


def test_parse_rss_and_atom():
    a = parse_feed(RSS)
    assert a[0]["title"].startswith("Oil jumps") and a[0]["published"] == "2026-10-05T21:46:22Z"
    assert "description" not in a[0]
    assert a[1]["published"] is None            # dropped later by the adapter, never given a fake time
    b = parse_feed(ATOM)
    assert b[0]["url"] == "https://ex.com/c" and b[0]["published"] == "2026-10-05T13:56:58Z"


def test_dates():
    assert parse_date("<![CDATA[Mon, 5 Oct 2026 20:30:00 GMT]]>") == "2026-10-05T20:30:00Z"
    assert parse_date("Mon, 05 Oct 2026  09:00:00 EST") == "2026-10-05T14:00:00Z"
    assert parse_date("2026-09-23T01:52:26Z") == "2026-09-23T01:52:26Z"
    assert parse_date("garbage") is None


def test_tagging():
    t = Tagger()
    r = t.tag("Oil jumps as Houthi missiles hit tanker in Red Sea", ["WORLD"])
    assert {"OIL", "WAR", "SHIPPING", "WORLD"} <= set(r["topics"]) and r["hot"]
    assert t.tag("Supreme Court takes up suit against Exxon", [])["tickers"] == ["XOM"]
    # generic words must not tag tickers
    assert t.tag("Freeport LNG ramps up gas intake", [])["tickers"] == []
    assert t.tag("Asian LNG prices climb", [])["tickers"] == []
    assert t.tag("Cheniere (LNG) beats estimates", [])["tickers"] == ["LNG"]
    assert t.tag("Shelling continues near the Niger Delta", [])["tickers"] == []
