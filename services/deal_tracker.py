"""Deal tracker — genuine discounts on the hardware / services WE will buy (GPUs, servers, VPS …).

Reads public deal RSS feeds (made for syndication) and matches each new item against the owner's
watch list; a match goes privately to the owner's bot chat. Nothing is scraped or resold — it is a
price alert for our own purchases.
  DEAL_FEEDS  comma-separated RSS URLs (default: Slickdeals front page)
Bot (owner chat):  /watch rtx 4090 1500   /watches   /unwatch 2   /deals
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from decouple import config

DEFAULT_FEEDS = "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"
FEEDS = [u.strip() for u in str(config("DEAL_FEEDS", default=DEFAULT_FEEDS)).split(",") if u.strip()]


def parse_rss(xml: str) -> List[Dict]:
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        m = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", title)
        out.append({"title": title, "link": link, "price": float(m.group(1).replace(",", "")) if m else None})
    return out


def matches(item: Dict, watch: Dict) -> bool:
    t = item["title"].lower()
    if not all(w in t for w in watch["words"]):
        return False
    return watch.get("max") is None or (item["price"] is not None and item["price"] <= watch["max"])


def parse_watch(args: List[str]) -> Optional[Dict]:
    if not args:
        return None
    mx = None
    if re.fullmatch(r"\$?[\d,]+(\.\d+)?", args[-1]) and len(args) > 1:
        mx = float(args[-1].lstrip("$").replace(",", ""))
        args = args[:-1]
    words = [a.lower() for a in args if a.strip()]
    return {"words": words, "max": mx} if words else None


async def _load() -> Dict:
    from services import data_store
    v, _ = await data_store.get("deal_watch")
    return v or {"watches": [], "seen": {}}


async def _save(v: Dict) -> None:
    from services import data_store
    v["seen"] = {k: t for k, t in v.get("seen", {}).items() if time.time() - t < 14 * 86400}
    await data_store.put("deal_watch", v)


async def add(watch: Dict) -> List[Dict]:
    v = await _load()
    v["watches"].append(watch)
    await _save(v)
    return v["watches"]


async def remove(index: int) -> List[Dict]:
    v = await _load()
    if 1 <= index <= len(v["watches"]):
        v["watches"].pop(index - 1)
    await _save(v)
    return v["watches"]


async def check(notify: bool = True) -> Dict:
    from services.data_feeds import _get
    v = await _load()
    if not v["watches"]:
        return {"watches": 0, "hits": []}
    items: List[Dict] = []
    for url in FEEDS:
        xml = await _get("deal_feed", url, as_json=False)
        if xml:
            items += parse_rss(xml)
    hits = []
    for it in items:
        key = it["link"] or it["title"]
        if key in v["seen"]:
            continue
        for w in v["watches"]:
            if matches(it, w):
                hits.append({**it, "watch": " ".join(w["words"])})
                v["seen"][key] = time.time()
                break
    await _save(v)
    if notify and hits:
        from services.owner_notify import send
        await send("🛒 Deals on your watch list\n" + "\n".join(
            f"• {h['title'][:120]}\n  {h['link']}" for h in hits[:8]))
    return {"watches": len(v["watches"]), "items": len(items), "hits": hits}
