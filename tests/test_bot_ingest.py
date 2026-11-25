import asyncio
import datetime as dt
import io
import zipfile

from services import calendar_paste as cp
from services import cme_ingest as ci

PASTE = """Date	12:00pm	Currency	Impact		Alerts	Detail	Actual	Forecast	Previous	Graph
Tue
Nov 24
6:00pm
USD		
CB Consumer Confidence
		92.4	91.7	
8:25pm
CAD		
BOC Gov Macklem Speaks
				
Wed
Nov 25
3:30am
AUD		
CPI m/m
		0.4%	0.8%	
AUD		
CPI y/y
		4.7%	4.3%	
4:30pm
USD		
Core PCE Price Index m/m
		0.3%	0.3%	
Thu
Nov 26
All Day
USD		
Bank Holiday
"""


def test_calendar_paste_parse():
    assert cp.looks_like_ff(PASTE)
    ev = cp.parse(PASTE, today=dt.date(2026, 11, 24), offset_h=3)
    by = {e["title"]: e for e in ev}
    assert by["Core PCE Price Index m/m"]["time_utc"] == "2026-11-25T13:30+00:00"      # 4:30pm UTC+3 → 13:30 UTC
    assert by["Core PCE Price Index m/m"]["impact"] == "High" and by["CPI y/y"]["ccy"] == "AUD"
    assert by["CPI y/y"]["time_utc"] == "2026-11-25T00:30+00:00"                        # inherits 3:30am
    assert by["BOC Gov Macklem Speaks"]["impact"] == "Medium" and by["Bank Holiday"]["impact"] == "Medium"
    assert by["CPI m/m"]["forecast"] == "0.4%" and by["CPI m/m"]["previous"] == "0.8%"
    assert not cp.looks_like_ff("hello there, how is the market?")


def test_section_detection_and_zip():
    assert ci.section_of("Section64_Metals_Option_Products.pdf", "") == 64
    assert ci.section_of("x.pdf", "PG39 BULLETIN # 226@ EURO FX") == 39
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cme/Section62_Metals_Futures_Products.pdf", b"%PDF-fake")
        z.writestr("__MACOSX/._junk.pdf", b"x")
    files = ci.pdfs_from("cme.zip", buf.getvalue())
    assert [n for n, _ in files] == ["Section62_Metals_Futures_Products.pdf"]
    assert ci.pdfs_from("note.txt", b"hello") == []


def test_ingest_pairs_wait_then_complete(monkeypatch):
    from tests.test_cme_bulletin import OPT, FUT
    from services import data_store
    store = {}

    async def put(k, v):
        store[k] = v
        return True
    monkeypatch.setattr(data_store, "put", put)
    ci._STAGE.clear()
    texts = {b"%PDF-64": OPT, b"%PDF-62": FUT}
    monkeypatch.setattr(ci, "_text", lambda pdf: texts[pdf])
    loop = asyncio.new_event_loop()
    r1 = loop.run_until_complete(ci.ingest([("Section64_x.pdf", b"%PDF-64")]))
    assert r1["waiting"] and not r1["updated"]
    r2 = loop.run_until_complete(ci.ingest([("Section62_x.pdf", b"%PDF-62")]))
    assert r2["updated"] and r2["updated"][0].startswith("gold") and "cme_gold_chain" in store
