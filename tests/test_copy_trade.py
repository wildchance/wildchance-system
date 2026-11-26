import asyncio

import pytest

from services import copy_trade as ct


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    from services import data_store
    store = {}

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    ct._master.clear(); ct._followers.clear(); ct._settings.clear(); ct._loaded["v"] = False
    yield store


MASTER = {"login": "100", "broker": "FundingPips", "equity": 5000, "ccy": "USD",
          "positions": [{"ticket": 77, "symbol": "EURUSD.r", "side": "SELL", "volume": 0.5, "open_price": 1.1650,
                         "sl": 1.1700, "tp": 1.1550, "contract_size": 100000, "open_time": 1}]}


def run(c):
    return asyncio.run(c)


def test_equity_scaled_targets_and_cent_accounts():
    run(ct.set_master(MASTER, now=1000))
    t = run(ct.targets("200", 1000, "USD", now=1001))
    p = t["positions"][0]
    assert t["action"] == "COPY" and p["symbol"] == "EURUSD" and p["id"] == "77"
    assert p["ratio"] == 0.2 and p["target_volume"] == 0.1
    cent = run(ct.targets("300", 100000, "USC", now=1001))           # $1,000 in cents
    assert cent["positions"][0]["target_volume"] == 0.1


def test_master_offline_holds_never_closes():
    run(ct.set_master(MASTER, now=1000))
    t = run(ct.targets("200", 1000, now=1000 + ct.STALE_S + 1))
    assert t["master_ok"] is False and t["action"] == "HOLD" and t["positions"] == []
    assert run(ct.targets("200", 1000, now=1000))["action"] == "COPY"
    assert run(ct.targets("100", 5000, now=1000))["reason"] == "this account is the master"


def test_pause_multiplier_and_persist(clean):
    run(ct.set_master(MASTER, now=1000))
    run(ct.control("200", paused=True))
    t = run(ct.targets("200", 1000, now=1000))
    assert t["action"] == "PAUSED" and t["positions"]                 # open copies still managed
    run(ct.control("200", paused=False, mult=0.5))
    assert run(ct.targets("200", 1000, now=1000))["positions"][0]["target_volume"] == 0.05
    assert clean["copy_accounts"]["200"] == {"paused": False, "mult": 0.5}
    run(ct.control("all", paused=True))
    assert all(v["paused"] for v in clean["copy_accounts"].values())


def test_status_and_text():
    run(ct.set_master(MASTER, now=1000))
    run(ct.report({"login": "200", "name": "Exness 1", "broker": "Exness", "equity": 100000, "ccy": "USC",
                   "positions": [{"ticket": 9, "master_id": "77"}], "missing": 1, "lag_ms": 420,
                   "daily_pl_pct": -0.4, "guard": "ACTIVE", "errors": ["no symbol for NAS100 - set InpSymbolMap"]}, now=1002))
    st = run(ct.status(now=1003))
    a = st["accounts"][0]
    assert st["master"]["online"] and a["online"] and a["equity_usd"] == 1000 and a["copies"] == 1 and a["missing"] == 1
    txt = ct.text(st)
    assert "Master FundingPips #100 🟢" in txt and "Exness 1 #200 $1,000" in txt and "1 not copied" in txt


def test_bridge_copy_routes_need_token(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import bridge
    monkeypatch.setattr(bridge, "TOKEN", "t0k")
    app = FastAPI()
    app.include_router(bridge.router)
    c = TestClient(app)
    assert c.post("/bridge/copy/master", json=MASTER).status_code == 403
    h = {"X-Bridge-Token": "t0k"}
    assert c.post("/bridge/copy/master", json=MASTER, headers=h).json() == {"ok": True, "positions": 1}
    r = c.get("/bridge/copy?login=200&equity=1000&ccy=USD", headers=h).json()
    assert r["action"] == "COPY" and '"target_volume":0.1' in c.get(
        "/bridge/copy?login=200&equity=1000", headers=h).text       # compact JSON the EA parses
    assert c.post("/bridge/copy/report", json={"login": "200"}, headers=h).json() == {"ok": True}


def test_copy_page_public_data_private():
    from services.access import is_public
    assert is_public("/copy") and not is_public("/copy/status") and not is_public("/copy/control")
    src = open("mt5_bridge/WildChance_Copier.mq5").read()
    assert "/bridge/copy/master" in src and "X-Bridge-Token" in src and "GvDone" in src


def test_copier_ignores_disconnected_zero():
    src = open("mt5_bridge/WildChance_Copier.mq5").read()
    assert "bool AccountReady()" in src and "if(!AccountReady())" in src and "GlobalVariableGet(k) <= 0" in src
