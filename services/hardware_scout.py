"""Hardware scout — refurbished servers / GPUs / parts to import, test and resell in Kenya.

SOURCE   eBay Browse API (official; free developer keys EBAY_CLIENT_ID / EBAY_CLIENT_SECRET,
         marketplace EBAY_MARKETPLACE = EBAY_US). Searches the owner's scout list (words, max USD,
         minimum seller feedback %) for fixed-price listings.
LANDED   item + eBay shipping + freight to Nairobi (SCOUT_FREIGHT_USD, servers SCOUT_FREIGHT_SERVER_USD)
         + forwarder fee (SCOUT_FORWARDER_USD) = CIF; import duty (SCOUT_DUTY_PCT, 0 by default — many
         computer parts are 0 % under the EAC tariff, CONFIRM with your clearing agent), VAT
         (SCOUT_VAT_PCT 16 % on CIF + duty), IDF (SCOUT_IDF_PCT 3.5 %) and RDL (SCOUT_RDL_PCT 2 %) on
         CIF, FX at KES_PER_USD with a SCOUT_FX_BUFFER_PCT (2 %) cushion. Every rate is a setting.
MARGIN   vs the owner's local selling price list (/price rtx 3090 145000 — the longest matching
         phrase wins); alert when margin ≥ SCOUT_MIN_MARGIN_PCT (25 %).
LEDGER   /bought, /sold, /stock, /ledger — landed cost, sale price, profit, VAT collected.
"""

from __future__ import annotations

import base64
import time
from typing import Dict, List, Optional

from decouple import config


def _f(name: str, default: str) -> float:
    return float(config(name, default=default))


EBAY_ID = config("EBAY_CLIENT_ID", default=None)
EBAY_SECRET = config("EBAY_CLIENT_SECRET", default=None)
MARKET = str(config("EBAY_MARKETPLACE", default="EBAY_US"))
_token = {"v": None, "exp": 0.0}


def rates() -> Dict[str, float]:
    return {"kes_per_usd": _f("KES_PER_USD", "129"), "fx_buffer": _f("SCOUT_FX_BUFFER_PCT", "2") / 100,
            "freight": _f("SCOUT_FREIGHT_USD", "40"), "freight_server": _f("SCOUT_FREIGHT_SERVER_USD", "150"),
            "forwarder": _f("SCOUT_FORWARDER_USD", "15"), "duty": _f("SCOUT_DUTY_PCT", "0") / 100,
            "vat": _f("SCOUT_VAT_PCT", "16") / 100, "idf": _f("SCOUT_IDF_PCT", "3.5") / 100,
            "rdl": _f("SCOUT_RDL_PCT", "2") / 100, "min_margin": _f("SCOUT_MIN_MARGIN_PCT", "25") / 100}


HEAVY = ("server", "poweredge", "proliant", "chassis", "rack", "1u", "2u", "4u", "workstation")


def landed_kes(price_usd: float, ship_usd: float, title: str, r: Optional[Dict] = None) -> Dict:
    r = r or rates()
    freight = r["freight_server"] if any(w in title.lower() for w in HEAVY) else r["freight"]
    cif = price_usd + ship_usd + freight + r["forwarder"]
    duty = cif * r["duty"]
    vat = (cif + duty) * r["vat"]
    idf, rdl = cif * r["idf"], cif * r["rdl"]
    total_usd = cif + duty + vat + idf + rdl
    fx = r["kes_per_usd"] * (1 + r["fx_buffer"])
    return {"cif_usd": round(cif, 2), "duty_usd": round(duty, 2), "vat_usd": round(vat, 2),
            "idf_usd": round(idf, 2), "rdl_usd": round(rdl, 2), "total_usd": round(total_usd, 2),
            "landed_kes": round(total_usd * fx), "input_vat_kes": round(vat * fx)}


def match_price(title: str, prices: Dict[str, float]) -> Optional[tuple]:
    t = title.lower()
    best = None
    for phrase, kes in prices.items():
        words = phrase.split()
        if all(w in t for w in words) and (best is None or len(words) > len(best[0].split())):
            best = (phrase, kes)
    return best


def parse_ebay(data: Dict) -> List[Dict]:
    out = []
    for it in (data or {}).get("itemSummaries") or []:
        try:
            price = float(it["price"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        ship = 0.0
        so = (it.get("shippingOptions") or [{}])[0]
        try:
            ship = float(((so.get("shippingCost") or {}).get("value")) or 0)
        except (TypeError, ValueError):
            ship = 0.0
        seller = it.get("seller") or {}
        out.append({"id": it.get("itemId"), "title": it.get("title", ""), "price_usd": price, "ship_usd": ship,
                    "condition": it.get("condition"), "url": it.get("itemWebUrl"),
                    "seller_pct": float(seller.get("feedbackPercentage") or 0),
                    "seller_score": int(seller.get("feedbackScore") or 0)})
    return out


def evaluate(item: Dict, prices: Dict[str, float], r: Optional[Dict] = None) -> Optional[Dict]:
    r = r or rates()
    m = match_price(item["title"], prices)
    if not m:
        return None
    lc = landed_kes(item["price_usd"], item["ship_usd"], item["title"], r)
    margin = (m[1] - lc["landed_kes"]) / m[1] if m[1] else -1
    return {**item, **lc, "sell_kes": m[1], "price_key": m[0], "margin": round(margin, 3),
            "profit_kes": round(m[1] - lc["landed_kes"]), "worth_it": margin >= r["min_margin"]}


# ── storage ──────────────────────────────────────────────────────────────────────
async def load() -> Dict:
    from services import data_store
    v, _ = await data_store.get("hw_scout")
    v = v or {}
    v.setdefault("watches", [])
    v.setdefault("prices", {})
    v.setdefault("seen", {})
    v.setdefault("ledger", [])
    return v


async def save(v: Dict) -> None:
    from services import data_store
    v["seen"] = {k: t for k, t in v["seen"].items() if time.time() - t < 21 * 86400}
    await data_store.put("hw_scout", v)


# ── eBay ───────────────────────────────────────────────────────────────────────
async def _ebay_token() -> Optional[str]:
    if _token["v"] and time.time() < _token["exp"] - 60:
        return _token["v"]
    if not (EBAY_ID and EBAY_SECRET):
        return None
    import httpx
    auth = base64.b64encode(f"{EBAY_ID}:{EBAY_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.ebay.com/identity/v1/oauth2/token",
                         headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
                         data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"})
    if r.status_code != 200:
        from services import errors
        errors.record("upstream_data", "ebay oauth", f"HTTP {r.status_code}")
        return None
    j = r.json()
    _token.update(v=j.get("access_token"), exp=time.time() + float(j.get("expires_in", 7200)))
    return _token["v"]


async def search(words: List[str], max_usd: Optional[float], limit: int = 50) -> List[Dict]:
    from services.data_feeds import _get
    tok = await _ebay_token()
    if not tok:
        return []
    flt = "buyingOptions:{FIXED_PRICE}" + (f",price:[..{max_usd:g}],priceCurrency:USD" if max_usd else "")
    data = await _get("ebay_browse", "https://api.ebay.com/buy/browse/v1/item_summary/search",
                      {"q": " ".join(words), "filter": flt, "limit": limit, "sort": "price"},
                      headers={"Authorization": f"Bearer {tok}", "X-EBAY-C-MARKETPLACE-ID": MARKET})
    return parse_ebay(data or {})


async def scout(notify: bool = True) -> Dict:
    v = await load()
    if not v["watches"]:
        return {"ok": False, "reason": "scout list empty — /scoutwatch dell r740 600"}
    if not (EBAY_ID and EBAY_SECRET):
        return {"ok": False, "reason": "set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (developer.ebay.com)"}
    r = rates()
    found, scanned = [], 0
    for w in v["watches"]:
        for it in await search(w["words"], w.get("max_usd")):
            scanned += 1
            if it["id"] in v["seen"] or it["seller_pct"] < w.get("min_seller", 98):
                continue
            ev = evaluate(it, v["prices"], r)
            v["seen"][it["id"]] = time.time()
            if ev and ev["worth_it"]:
                found.append(ev)
    await save(v)
    found.sort(key=lambda e: -e["margin"])
    if notify and found:
        from services.owner_notify import send
        await send(card(found[:6]))
    return {"ok": True, "scanned": scanned, "opportunities": found[:20]}


def card(found: List[Dict]) -> str:
    lines = ["🔧 Hardware scout — margin after landing in Nairobi"]
    for e in found:
        lines.append(f"🟢 {e['title'][:70]}\n   ${e['price_usd']:g} + ${e['ship_usd']:g} ship → landed KES {e['landed_kes']:,} "
                     f"→ sells KES {e['sell_kes']:,.0f} · margin {e['margin']:.0%} (KES {e['profit_kes']:,}) · "
                     f"seller {e['seller_pct']:g}%\n   {e['url']}")
    return "\n".join(lines)


# ── ledger ─────────────────────────────────────────────────────────────────────
def ledger_totals(ledger: List[Dict], vat_registered: bool) -> Dict:
    sold = [x for x in ledger if x.get("sold_kes")]
    stock = [x for x in ledger if not x.get("sold_kes")]
    revenue = sum(x["sold_kes"] for x in sold)
    cost = sum(x["cost_kes"] for x in sold)
    out_vat = round(revenue * 16 / 116) if vat_registered else 0
    return {"items": len(ledger), "in_stock": len(stock), "stock_value_kes": sum(x["cost_kes"] for x in stock),
            "sold": len(sold), "revenue_kes": revenue, "cost_kes": cost, "profit_kes": revenue - cost - out_vat,
            "output_vat_kes": out_vat}
