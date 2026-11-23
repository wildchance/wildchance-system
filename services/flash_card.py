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


async def post(symbol: str, side: str, open_price: float, close_price: float,
               when: Optional[dt.datetime] = None) -> Dict:
    """Send the card as a Telegram photo. Losing / flat trades are never posted."""
    if pips(symbol, side, open_price, close_price) <= 0:
        return {"sent": False, "reason": "not a winning trade"}
    from services.telegram_service import BOT_TOKEN, CHAT_ID
    chat = config("FLASH_CHAT_ID", default=None) or CHAT_ID
    if not BOT_TOKEN or not chat:
        return {"sent": False, "reason": "telegram not configured"}
    import httpx
    png = render(symbol, side, open_price, close_price, when)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                             data={"chat_id": chat, "caption": caption(symbol, side, open_price, close_price)},
                             files={"photo": ("flash.png", png, "image/png")})
        return {"sent": r.status_code == 200, "status": r.status_code}
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
    posted, skipped = [], []
    for g in group_deals(fresh):
        if g["net"] <= 0 or pips(g["symbol"], g["side"], g["open"], g["close"]) <= 0:
            skipped.append({"symbol": g["symbol"], "side": g["side"], "why": "not a winning close"})
            continue
        when = dt.datetime.fromtimestamp(g["close_time"], dt.timezone.utc) if g["close_time"] else None
        r = await post(g["symbol"], g["side"], g["open"], g["close"], when)
        (posted if r.get("sent") else skipped).append({"symbol": g["symbol"], "side": g["side"],
                                                       "pips": pips(g["symbol"], g["side"], g["open"], g["close"]),
                                                       **({} if r.get("sent") else {"why": r.get("reason")})})
    seen = (seen + [str(d.get("ticket")) for d in fresh])[-2000:]
    await data_store.put("flash_tickets", seen)
    return {"received": len(deals or []), "new": len(fresh), "posted": posted, "skipped": skipped}
