import asyncio
import io
import zipfile

from services import cme_slate as cs

D1 = open("tests/fixtures/slate_day1.xlsx", "rb").read()
D2 = open("tests/fixtures/slate_day2.xlsx", "rb").read()


def _store(monkeypatch):
    from services import data_store
    store = {}

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    return store


def test_reads_slate_and_sums_weeklies():
    (day, prods), = cs.slates_from("Product Slate Export.xlsx", D1)
    assert day == "2026-11-25" and len(prods) == 5
    m = cs.summarise(prods)
    assert m["EURUSD"] == {"fut_vol": 263509, "fut_oi": 926089, "opt_vol": 13362, "opt_oi": 470611}
    assert m["NZDUSD"]["opt_oi"] == 7895 and cs.depth(7895) == "thin" and cs.depth(470611) == "deep"


def test_zip_of_exports_and_non_slate_ignored():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("Product Slate Export.xlsx", D1)
        z.writestr("notes.txt", "hi")
    assert len(cs.slates_from("export.zip", b.getvalue())) == 1
    assert cs.slates_from("bulletin.pdf", b"%PDF-1.4") == []


def test_participation_needs_two_days(monkeypatch):
    store = _store(monkeypatch)
    r1 = asyncio.run(cs.ingest([("a.xlsx", D1)]))
    r2 = asyncio.run(cs.ingest([("b.xlsx", D2)]))
    assert r1["previous"] is None and "needs two days" in cs.reply_text(r1)
    eur = next(r for r in r2["markets"] if r["symbol"] == "EURUSD")
    assert r2["previous"] == "2026-11-25" and eur["participation"].startswith("OI +2.6%")
    assert set(store["cme_slate"]) == {"2026-11-25", "2026-11-26"}


def test_participation_reads():
    assert "covering" in cs.participation({"fut_oi": 1000}, {"fut_oi": 900})
    assert "new longs" in cs.participation({"fut_oi": 1000}, {"fut_oi": 1100}, price_chg=0.01)
    assert "flat" in cs.participation({"fut_oi": 1000}, {"fut_oi": 1001})
