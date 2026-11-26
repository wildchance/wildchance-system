"""Profit FLASH CARD — the owner's layout, WildChance-branded, posted as a photo.

Layout (from the owner's sample cards): dark card · brand mark top-left · SYMBOL +
BUY/SELL badge · big '+ N pips' · open / close price · name + timestamp · QR code to
the channel. Rendered server-side with Pillow (already installed via qrcode[pil]).

Pips: FX 0.0001 (JPY 0.01), gold 0.1, BTC $1, indices 0.1 (the sample: US30
42,512.00 → 42,818.43 = +3,064.3 pips). Only WINNING trades get a card.

Env: FLASH_NAME (default 'Wildchance'), FLASH_QR_URL (channel / join link),
FLASH_CHAT_ID (group to post to; defaults to TELEGRAM_CHAT_ID), FLASH_CARDS
(true/false — auto-post when a sniper case resolves 'held').
"""

from __future__ import annotations

import datetime as dt
import time
import io
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from decouple import config

NAME = str(config("FLASH_NAME", default="Wildchance"))
QR_URL = config("FLASH_QR_URL", default=None)
ENABLED = str(config("FLASH_CARDS", default="true")).lower() in ("1", "true", "yes", "on")
W, H = 1100, 1200                                   # 2× the sample (550×600) for sharpness
BG, GREEN, RED, WHITE, GREY = (17, 20, 26), (46, 204, 64), (231, 76, 60), (255, 255, 255), (150, 156, 166)


def pip_of(symbol: str) -> float:
    from propfirm.target_lots import pip_size
    return pip_size(symbol) or 0.1


def pips(symbol: str, side: str, open_price: float, close_price: float) -> float:
    move = (close_price - open_price) if side.upper() in ("BUY", "LONG") else (open_price - close_price)
    return round(move / pip_of(symbol), 1)


def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)       # Pillow ≥ 10.1 bundled TrueType
    except TypeError:
        return ImageFont.load_default()


def _price(symbol: str, v: float) -> str:
    from routes.cbdr import px
    return px(symbol, v)


def render(symbol: str, side: str, open_price: float, close_price: float,
           when: Optional[dt.datetime] = None, name: str = NAME, qr_url: Optional[str] = QR_URL) -> bytes:
    """PNG bytes of the card."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # background swoosh: concentric green arcs bottom-right (the sample's wave)
    for i in range(0, 260, 6):
        shade = (20, 60 + i // 3, 30 + i // 6)
        d.arc((820 - i, 560 + i // 2, 1960 + i, 2200 + i), 180, 270, fill=shade, width=2)
    # brand mark
    d.polygon([(80, 150), (110, 95), (135, 95), (105, 150)], fill=GREEN)
    d.polygon([(118, 150), (148, 95), (173, 95), (143, 150)], fill=WHITE)
    d.polygon([(156, 150), (186, 95), (211, 95), (181, 150)], fill=RED)
    d.text((240, 92), "WildChance", font=_font(56), fill=GREEN)
    d.text((240 + d.textlength("WildChance", font=_font(56)) + 12, 92), "Signals",
           font=_font(56), fill=WHITE)
    # symbol + side badge
    sym = symbol.upper().replace("/", "")
    d.text((80, 300), sym, font=_font(44), fill=WHITE)
    bx = 80 + d.textlength(sym, font=_font(44)) + 40
    buy = side.upper() in ("BUY", "LONG")
    col = GREEN if buy else RED
    d.rounded_rectangle((bx, 290, bx + 125, 350), radius=8, outline=col, width=3,
                        fill=(20, 60, 30) if buy else (70, 25, 25))
    d.text((bx + 22, 302), "BUY" if buy else "SELL", font=_font(32), fill=WHITE)
    # big pips
    p = pips(symbol, side, open_price, close_price)
    big = f"{p:,.1f} pips"
    size = 120
    while size > 60 and 170 + d.textlength(big, font=_font(size)) > W - 60:   # shrink to fit
        size -= 6
    d.text((80, 425 + (120 - size) // 2), "+", font=_font(size), fill=GREEN)
    d.text((80 + d.textlength("+", font=_font(size)) + 14, 420 + (120 - size) // 2), big,
           font=_font(size), fill=WHITE)
    # prices
    d.text((80, 610), "Open price:", font=_font(32, False), fill=GREY)
    d.text((340, 610), _price(symbol, open_price), font=_font(32), fill=WHITE)
    d.text((80, 710), "Close price:", font=_font(32, False), fill=GREY)
    d.text((340, 710), _price(symbol, close_price), font=_font(32), fill=WHITE)
    # avatar (initials) + name + time
    d.ellipse((80, 930, 210, 1060), fill=(36, 42, 52), outline=GREEN, width=3)
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "W"
    f_in = _font(50)
    d.text((145 - d.textlength(initials, font=f_in) / 2, 965), initials, font=f_in, fill=WHITE)
    when = (when or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    d.text((240, 930), name, font=_font(32), fill=WHITE)
    d.text((240, 990), when.strftime("%B %-d, %-I:%M %p ET"), font=_font(30), fill=GREY)
    # QR to the channel
    if qr_url:
        try:
            import qrcode
            q = qrcode.make(qr_url).convert("RGB").resize((200, 200))
            d.rectangle((770, 840, 990, 1060), fill=WHITE)
            img.paste(q, (780, 850))
        except Exception:
            pass
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def caption(symbol: str, side: str, open_price: float, close_price: float) -> str:
    return (f"✅ {symbol.upper()} {side.upper()} +{pips(symbol, side, open_price, close_price):,.1f} pips "
            f"({_price(symbol, open_price)} → {_price(symbol, close_price)})")


_post_lock = None
DEDUPE_H = float(config("FLASH_DEDUPE_H", default="24"))
COPY_OWNER = str(config("FLASH_COPY_OWNER", default="true")).lower() in ("1", "true", "yes", "on")


async def _already_posted(key: str) -> bool:
    """Same symbol + side + open + close within FLASH_DEDUPE_H → never post the card twice
    (two engines tracking one trade, or two checks running at once)."""
    import time as _t
    from services import data_store
    seen, _ = await data_store.get("flash_sent")
    seen = {k: v for k, v in (seen or {}).items() if _t.time() - v < DEDUPE_H * 3600}
    if key in seen:
        return True
    seen[key] = _t.time()
    await data_store.put("flash_sent", seen)
    return False


async def _copy_to_owner(png: bytes, cap: str, name: str) -> bool:
    """A downloadable copy in the owner's private chat with the bot (the channel blocks saving)."""
    if not COPY_OWNER:
        return False
    try:
        import httpx
        from services.owner_notify import BOT_TOKEN, OWNER_CHAT_ID
        if not (BOT_TOKEN and OWNER_CHAT_ID):
            return False
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                             data={"chat_id": OWNER_CHAT_ID, "caption": cap},
                             files={"document": (name, png, "image/png")})
        return r.status_code == 200
    except Exception:
        return False


async def latest_win(symbol: Optional[str] = None) -> Optional[Dict]:
    """The most recent tracked signal that hit at least TP1 (optionally for one symbol)."""
    from services import signal_tracker
    want = (symbol or "").upper().replace("/", "")
    rows = [r for r in await signal_tracker._load()
            if r.get("hits") and (not want or str(r.get("symbol", "")).upper().replace("/", "") == want)]
    if not rows:
        return None
    r = max(rows, key=lambda x: x["hits"][-1].get("ts") or 0)
    h = r["hits"][-1]
    return {"symbol": r["symbol"], "side": r["side"], "open": float(r["entry"]), "close": float(h["price"]),
            "tp": h.get("tp"), "ts": h.get("ts"), "engine": r.get("engine") or r.get("source")}


async def send_test(symbol: Optional[str] = None, side: Optional[str] = None,
                    open_price: Optional[float] = None, close_price: Optional[float] = None) -> Dict:
    """Owner-only TEST: render a flash card and send it to the owner's private bot chat
    (photo preview + downloadable file). Never the channel, never recorded as posted."""
    import httpx
    from services.owner_notify import BOT_TOKEN, OWNER_CHAT_ID
    if not (BOT_TOKEN and OWNER_CHAT_ID):
        return {"sent": False, "reason": "TELEGRAM_BOT_TOKEN / OWNER_CHAT_ID not set"}
    if open_price is None or close_price is None or not side:
        w = await latest_win(symbol)
        if not w:
            return {"sent": False, "reason": f"no tracked {symbol or ''} signal has hit TP1 yet — "
                                             "pass side/open/close to test with your own numbers".replace("  ", " ")}
        symbol, side, open_price, close_price = w["symbol"], w["side"], w["open"], w["close"]
        when = dt.datetime.fromtimestamp(w["ts"], dt.timezone.utc) if w.get("ts") else None
        label = f"TP{w.get('tp')} hit" + (f" · {w['engine']}" if w.get("engine") else "")
    else:
        when, label = None, "manual numbers"
    if pips(symbol, side, open_price, close_price) <= 0:
        return {"sent": False, "reason": "not a winning trade — flash cards are winners only"}
    png = render(symbol, side, open_price, close_price, when)
    cap = "🧪 TEST (only you see this) — " + caption(symbol, side, open_price, close_price) + f"\n{label}"
    name = f"wildchance-{symbol.replace('/', '')}-{side.lower()}-test.png"
    async with httpx.AsyncClient(timeout=20) as c:
        p = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                         data={"chat_id": OWNER_CHAT_ID, "caption": cap},
                         files={"photo": ("flash.png", png, "image/png")})
        d = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                         data={"chat_id": OWNER_CHAT_ID, "caption": "downloadable copy"},
                         files={"document": (name, png, "image/png")})
    return {"sent": p.status_code == 200, "photo_status": p.status_code, "file_status": d.status_code,
            "symbol": symbol, "side": side, "open": open_price, "close": close_price,
            "pips": pips(symbol, side, open_price, close_price), "source": label}


MATCH_PIPS = float(config("FLASH_MATCH_PIPS", default="15"))


def same_trade(recent: list, symbol: str, side: str, open_price: float, now: float,
               sources: tuple = ("signal", "deal")) -> bool:
    """A broker close matches a card already posted for the same trade (the signal tracker
    posted it at its signal entry; the broker fill differs by slippage, or a later partial)."""
    sym, sd = symbol.upper().replace("/", ""), side.upper()
    for r in recent or []:
        if r.get("sym") == sym and r.get("side") == sd and r.get("src", "signal") in sources and now - float(r.get("ts") or 0) < DEDUPE_H * 3600 \
                and abs(pips(sym, sd, float(r["open"]), open_price)) <= MATCH_PIPS:
            return True
    return False


async def _same_trade_posted(symbol: str, side: str, open_price: float, source: str) -> bool:
    """deal → blocked by any earlier card for the trade (tracker TP card or an earlier partial);
    signal → blocked only by a broker card (the tracker still posts TP1, TP2, TP3 of its own)."""
    import time as _t
    from services import data_store
    now = _t.time()
    recent, _ = await data_store.get("flash_recent")
    recent = [r for r in (recent or []) if now - float(r.get("ts") or 0) < DEDUPE_H * 3600]
    hit = same_trade(recent, symbol, side, open_price, now,
                     ("signal", "deal") if source == "deal" else ("deal",))
    if not hit:
        recent.append({"sym": symbol.upper().replace("/", ""), "side": side.upper(),
                       "open": float(open_price), "ts": now, "src": source})
        await data_store.put("flash_recent", recent[-500:])
    return hit


async def post(symbol: str, side: str, open_price: float, close_price: float,
               when: Optional[dt.datetime] = None, source: str = "signal") -> Dict:
    """Send the card as a Telegram photo. Losing / flat trades are never posted.
    source='deal' (a broker close from the EA) is skipped when a card for the same trade
    (same symbol + side, entry within FLASH_MATCH_PIPS, last FLASH_DEDUPE_H) already went out."""
    import asyncio
    global _post_lock
    if pips(symbol, side, open_price, close_price) <= 0:
        return {"sent": False, "reason": "not a winning trade"}
    _post_lock = _post_lock or asyncio.Lock()
    key = f"{symbol.upper().replace('/', '')}:{side.upper()}:{round(open_price, 4)}:{round(close_price, 4)}"
    async with _post_lock:
        try:
            if await _already_posted(key):
                return {"sent": False, "reason": "duplicate card (already posted)"}
            if await _same_trade_posted(symbol, side, open_price, source):
                return {"sent": False, "reason": "same trade already carded"}
        except Exception:
            pass
    from services.telegram_service import BOT_TOKEN, CHAT_ID
    chat = config("FLASH_CHAT_ID", default=None) or CHAT_ID
    if not BOT_TOKEN or not chat:
        return {"sent": False, "reason": "telegram not configured"}
    import httpx
    png = render(symbol, side, open_price, close_price, when)
    try:
        cap = caption(symbol, side, open_price, close_price)
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                             data={"chat_id": chat, "caption": cap},
                             files={"photo": ("flash.png", png, "image/png")})
        stamp = (when or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%d-%H%M")
        owner = await _copy_to_owner(png, cap, f"wildchance-{symbol.replace('/', '')}-{side.lower()}-{stamp}.png")
        return {"sent": r.status_code == 200, "status": r.status_code, "owner_copy": owner}
    except Exception as e:
        return {"sent": False, "reason": str(e)[:120]}



FROM_DEALS = str(config("FLASH_FROM_DEALS", default="true")).lower() in ("1", "true", "yes", "on")


def group_deals(deals) -> list:
    """Closed broker deals → one entry per symbol + side in this batch (split positions,
    e.g. three EURUSD 0.01 sells, become ONE card). Volume-weighted open / close prices;
    net = profit + commission + swap."""
    from services.broker_feed import canon
    groups = {}
    for d in deals or []:
        try:
            sym = canon(d["symbol"])
            side = str(d["side"]).upper()
            vol = float(d.get("volume") or 0) or 0.01
            g = groups.setdefault((sym, side), {"symbol": sym, "side": side, "vol": 0.0, "o": 0.0, "c": 0.0,
                                                "net": 0.0, "tickets": [], "close_time": 0})
            g["vol"] += vol
            g["o"] += vol * float(d["open_price"])
            g["c"] += vol * float(d["close_price"])
            g["net"] += float(d.get("net") or 0)
            g["tickets"].append(str(d["ticket"]))
            g["close_time"] = max(g["close_time"], int(d.get("close_time") or 0))
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for g in groups.values():
        out.append({"symbol": g["symbol"], "side": g["side"], "open": g["o"] / g["vol"],
                    "close": g["c"] / g["vol"], "net": round(g["net"], 2), "lots": round(g["vol"], 2),
                    "tickets": g["tickets"], "close_time": g["close_time"]})
    return out


MAX_AGE_MIN = int(config("FLASH_MAX_AGE_MIN", default="120"))   # only closes this fresh get a card


async def post_from_deals(deals) -> dict:
    """Flash cards from the trader's REAL broker closes (any source — sniper, other
    signals, manual). Only net-profitable groups with positive pips; each ticket is
    posted once ever (remembered in Neon)."""
    if not FROM_DEALS:
        return {"posted": 0, "reason": "FLASH_FROM_DEALS off"}
    from services import data_store
    seen, _ = await data_store.get("flash_tickets")
    seen = list(seen or [])
    fresh = [d for d in deals or [] if str(d.get("ticket")) not in set(seen)]
    cutoff = time.time() - MAX_AGE_MIN * 60          # a history backfill goes to the journal, not the channel
    recent = [d for d in fresh if int(d.get("close_time") or 0) >= cutoff]
    posted, skipped = [], []
    for g in group_deals(recent):
        if g["net"] <= 0 or pips(g["symbol"], g["side"], g["open"], g["close"]) <= 0:
            skipped.append({"symbol": g["symbol"], "side": g["side"], "why": "not a winning close"})
            continue
        when = dt.datetime.fromtimestamp(g["close_time"], dt.timezone.utc) if g["close_time"] else None
        r = await post(g["symbol"], g["side"], g["open"], g["close"], when, source="deal")
        (posted if r.get("sent") else skipped).append({"symbol": g["symbol"], "side": g["side"],
                                                       "pips": pips(g["symbol"], g["side"], g["open"], g["close"]),
                                                       **({} if r.get("sent") else {"why": r.get("reason")})})
    seen = (seen + [str(d.get("ticket")) for d in fresh])[-2000:]
    await data_store.put("flash_tickets", seen)
    return {"received": len(deals or []), "new": len(fresh), "stale": len(fresh) - len(recent),
            "posted": posted, "skipped": skipped}
