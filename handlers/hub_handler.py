"""WildChance bot = the owner's hub. Every command reads the CURRENT system (signal tracker,
strength matrix, rotation, CME / screen walls, stored calendar, error tracker) — the old
USD/JPY-only forward-test commands are retired. Owner-only commands answer only in the chat
whose id is OWNER_CHAT_ID; everyone else gets the public reads.

Public:  /start /help /strength /rotation /walls EURUSD /calendar [USD,EUR] /results /recent
         /edge /terminal
Owner:   /open (open tracked signals + exposure) /health (errors today) /audit (data audit)
Old names kept as aliases: /summary /profit /wins → /results, /history /last5 → /recent
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Optional

from decouple import config
from telegram import BotCommand, Update
from telegram.ext import ContextTypes

OWNER_CHAT_ID = str(config("OWNER_CHAT_ID", default="") or "")
BASE = (config("WEBHOOK_BASE_URL", default=None) or config("RENDER_EXTERNAL_URL", default=None)
        or "https://wildchance-system.onrender.com").rstrip("/")

PUBLIC_COMMANDS = [("strength", "Currency strength + best pairings"), ("rotation", "Event rotation right now"),
                   ("walls", "Dealer walls: /walls EURUSD"), ("calendar", "High-impact events (7 days)"),
                   ("results", "Track record: wins / losses / BE"), ("recent", "Last 10 closed signals"),
                   ("edge", "COT + retail bias board"), ("terminal", "Open the terminal"), ("help", "Show menu")]
OWNER_COMMANDS = [("open", "Open signals + exposure (owner)"), ("health", "Errors today (owner)"),
                  ("audit", "Data audit (owner)")]


def is_owner(update: Update) -> bool:
    return bool(OWNER_CHAT_ID) and str(update.effective_chat.id) == OWNER_CHAT_ID


async def _reply(update: Update, text: str) -> None:
    try:
        await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception:                                    # Markdown broke → plain text
        await update.message.reply_text(text.replace("*", "").replace("_", "").replace("`", ""),
                                        disable_web_page_preview=True)


async def _guard(update: Update, fn) -> None:
    try:
        await fn()
    except Exception as e:
        from services import errors
        errors.swallow(f"bot {update.message.text if update.message else '?'}", e)
        await update.message.reply_text(f"⚠️ That read failed ({type(e).__name__}) — it is logged; try again shortly.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["👋 *WildChance hub*", "",
             "💪 /strength — currency strength + best pairings",
             "⏸ /rotation — which currencies step aside for news, what to trade instead",
             "🧱 /walls EURUSD — dealer walls (CME or vol screens)",
             "🗓 /calendar [USD,EUR] — high-impact events, next 7 days",
             "📈 /results — track record (wins · losses · breakevens · pips)",
             "🗒 /recent — last 10 closed signals",
             "🧭 /edge — COT + retail bias board",
             "🖥 /terminal — open the terminal"]
    if is_owner(update):
        lines += ["", "*Owner*", "📂 /open — open signals + exposure", "🩺 /health — errors today",
                  "🧾 /audit — data audit", "",
                  "📥 Send CME bulletin PDFs (or a .zip) here → dealer walls update.",
                  "🗓 Paste the Forex Factory week here → calendar + blackouts update.",
                  "🛒 /watch rtx 4090 1500 · /watches · /unwatch 2 · /deals — hardware deal alerts",
                  "🆔 /chatid inside a group or topic → ids for GROUP_ROUTES",
                  "🔧 Hardware: /scoutwatch dell r740 600 · /price r740 115000 · /scout · /scoutlist",
                  "📦 /bought desc 98000 · /sold 3 145000 · /stock · /ledger",
                  "_Flash cards, backups and daily reports arrive here as files._"]
    await _reply(update, "\n".join(lines))


async def strength_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services import fx_rotation as fx
        st = await fx.strength()
        await _reply(update, fx.card(st))
    await _guard(update, go)


async def rotation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services import fx_rotation as fx
        rot = await fx.rotation()
        if not rot.get("blocked"):
            await _reply(update, "⏸ *Event rotation*: no high-impact release in the next 3 h — the full matrix is open.")
            return
        await _reply(update, fx.card(await fx.strength(), rot))
    await _guard(update, go)


async def walls_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services.options_levels import run
        sym = (context.args[0] if context.args else "EURUSD").upper().replace("/", "")
        if len(sym) == 6 and sym.isalpha():
            sym = f"{sym[:3]}/{sym[3:]}"
        r = await run(sym)
        if not r.get("ok"):
            await _reply(update, f"🧱 {sym}: {r.get('reason', 'no levels')}")
            return
        lines = [f"🧱 *Dealer walls — {sym}* ({r.get('underlying')}, {r.get('asof') or r.get('expiry') or 'live'})"]
        if r.get("put_call_ratio") is not None:
            lines.append(f"Put/call {r['put_call_ratio']}")
        for x in r.get("levels", [])[:9]:
            lines.append(f"`{x['price']}`  {x['label']}")
        await _reply(update, "\n".join(lines))
    await _guard(update, go)


async def calendar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services import econ_calendar as ec
        await ec.ensure_loaded()
        ccys = {c.strip().upper() for c in context.args[0].split(",")} if context.args else None
        ev = [e for e in ec.upcoming(7, high_only=True) if not ccys or e["ccy"] in ccys]
        if not ev:
            await _reply(update, "🗓 No high-impact releases in the next 7 days"
                         + (f" for {', '.join(sorted(ccys))}." if ccys else "."))
            return
        await _reply(update, ec.week_card(ev[:20]))
    await _guard(update, go)


async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services import signal_tracker as stk
        s = stk.summarise(await stk._load())
        if not s.get("closed"):
            await _reply(update, f"📈 *Track record*: no closed signals yet ({s.get('open', 0)} open). "
                         "Every card is followed to TP / SL on live prices.")
            return
        await _reply(update, "\n".join(stk.report_lines(s)))
    await _guard(update, go)


async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services import signal_tracker as stk
        done = sorted([r for r in await stk._load() if r["result"] in ("win", "loss", "breakeven")],
                      key=lambda r: r.get("closed_at") or 0, reverse=True)[:10]
        if not done:
            await _reply(update, "🗒 No closed signals yet.")
            return
        icon = {"win": "✅", "loss": "❌", "breakeven": "➖"}
        lines = ["🗒 *Last closed signals*"]
        for r in done:
            when = dt.datetime.fromtimestamp(r.get("closed_at") or 0, dt.timezone.utc).strftime("%d %b %H:%M")
            lines.append(f"{icon[r['result']]} {r['symbol']} {r['side']} {r.get('pips') or 0:+.1f}p · {when} · {r['source']}")
        await _reply(update, "\n".join(lines))
    await _guard(update, go)


async def edge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services.wildchance_service import get_latest_feed
        feed = await get_latest_feed() or {}
        sig = sorted(feed.get("signals") or [], key=lambda s: -(s.get("confidence") or 0))
        if not sig:
            await _reply(update, "🧭 The COT / retail feed has not been scraped yet — it refreshes every 6 h.")
            return
        lines = ["🧭 *COT + retail bias board*"]
        for s in sig[:10]:
            ic = "🟢" if s.get("verdict") == "LONG" else "🔴" if s.get("verdict") == "SHORT" else "➖"
            lines.append(f"{ic} *{s['pair']}* {s.get('verdict')} conf {s.get('confidence')} · retail long "
                         f"{s.get('retail_long')}%" + (" · COT agrees" if s.get("cot_agrees") else ""))
        await _reply(update, "\n".join(lines))
    await _guard(update, go)


async def terminal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = f"🖥 Subscriber terminal: {BASE}/members/terminal"
    if is_owner(update):
        text += f"\n🔐 Owner terminal: {BASE}/terminal  (open it once with ?token=… — the browser remembers it)"
    await _reply(update, text)


async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return

    async def go():
        from services import fx_rotation as fx, signal_tracker as stk
        rows = [r for r in await stk._load() if r["result"] == "open"]
        if not rows:
            await _reply(update, "📂 No open tracked signals.")
            return
        lines = ["📂 *Open signals*"]
        for r in rows[-15:]:
            tp = f" · TP{len(r['hits'])} hit" if r.get("hits") else ""
            lines.append(f"{r['symbol']} {r['side']} @ {r['entry']} · SL {r['stop_now']}{tp} · {r['source']}"
                         + ("" if r.get("filled", True) else " · pending"))
        ex = fx.net_exposure(rows)
        if ex:
            lines.append("🧮 Exposure: " + ", ".join(f"{c} {v:+g}" for c, v in ex.items()))
        await _reply(update, "\n".join(lines))
    await _guard(update, go)


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return
    from services import errors
    s = errors.summary()
    lines = [f"🩺 *Health today*: {s['total']} problem events"]
    lines += [f"• {t['kind']} × {t['n']} — {t['where']}" for t in s["top"][:10]]
    await _reply(update, "\n".join(lines) if s["total"] else "🩺 Health today: no crashes, failed jobs or slow calls. 👍")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return

    async def go():
        from services import data_audit as da
        await _reply(update, da.message(await da.run()))
    await _guard(update, go)


HANDLERS = {"start": help_cmd, "help": help_cmd, "strength": strength_cmd, "rotation": rotation_cmd,
            "walls": walls_cmd, "calendar": calendar_cmd, "results": results_cmd, "recent": recent_cmd,
            "edge": edge_cmd, "board": edge_cmd, "terminal": terminal_cmd, "open": open_cmd,
            "health": health_cmd, "audit": audit_cmd,
            # old names → new reads
            "summary": results_cmd, "profit": results_cmd, "wins": results_cmd,
            "history": recent_cmd, "last5": recent_cmd, "signal": recent_cmd}


async def register_menu(bot) -> None:
    """Replace the stale command menu (phones cache the old list) with the hub commands;
    the owner's own chat also lists the owner commands."""
    from telegram import BotCommandScopeChat, BotCommandScopeDefault
    await bot.set_my_commands([BotCommand(c, d) for c, d in PUBLIC_COMMANDS], scope=BotCommandScopeDefault())
    if OWNER_CHAT_ID:
        try:
            await bot.set_my_commands([BotCommand(c, d) for c, d in PUBLIC_COMMANDS + OWNER_COMMANDS],
                                      scope=BotCommandScopeChat(int(OWNER_CHAT_ID)))
        except Exception:
            pass



async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ids for GROUP_ROUTES: type /chatid inside a group or a forum topic."""
    chat = update.effective_chat
    thread = getattr(update.message, "message_thread_id", None) if update.message else None
    ref = f"{chat.id}:{thread}" if thread else f"{chat.id}"
    await update.message.reply_text(
        f"🆔 chat id: {chat.id}\ntopic id: {thread or '— (not a topic)'}\ntype: {getattr(chat, 'type', '?')}\n"
        f"Use in GROUP_ROUTES as \"{ref}\"")


def _private(update: Update) -> bool:
    return getattr(update.effective_chat, "type", "private") == "private"


async def document_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner sends CME bulletin PDFs (or a .zip) → dealer-level chains; a Product Slate
    export (.xlsx or a zip of them) → daily volume / OI per market."""
    if not _private(update):
        return                                           # files in groups are not for the bot
    if not is_owner(update):
        await _reply(update, "🔒 Files are accepted from the owner only.")
        return
    doc = update.message.document

    async def go():
        from services import cme_ingest
        f = await doc.get_file()
        blob = bytes(await f.download_as_bytearray())
        from services import cme_slate
        name = doc.file_name or "file.pdf"
        if cme_slate.slates_from(name, blob):                 # CME Product Slate export(s)
            await update.message.reply_text(cme_slate.reply_text(await cme_slate.ingest([(name, blob)])))
            return
        rep = await cme_ingest.ingest([(name, blob)])
        await update.message.reply_text(cme_ingest.reply_text(rep))
    await _guard(update, go)


async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner pastes the Forex Factory week → stored as calendar events; anything else → /help."""
    if not _private(update):
        return                                           # never chat back to every group message
    text = update.message.text or ""
    from services import calendar_paste as cp
    if is_owner(update) and cp.looks_like_ff(text):
        async def go():
            ev = cp.parse(text)
            r = await cp.store(ev)
            await _reply(update, f"🗓 Calendar paste: {r['added']} releases stored ({r['high']} high impact) — "
                                 "blackouts and rotation use them now. Check with /calendar.")
        await _guard(update, go)
        return
    await update.message.reply_text("Type /help for the menu.")


HANDLERS["chatid"] = chatid_cmd


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return
    from services import deal_tracker as dt_
    w = dt_.parse_watch(context.args or [])
    if not w:
        await _reply(update, "Usage: /watch rtx 4090 1500  (words to match, optional max price)")
        return
    ws = await dt_.add(w)
    await _reply(update, f"🛒 Watching: {' '.join(w['words'])}" + (f" ≤ ${w['max']:g}" if w.get("max") else "")
                 + f" · {len(ws)} on the list")


async def watches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return
    from services import deal_tracker as dt_
    ws = (await dt_._load())["watches"]
    await _reply(update, "🛒 Watch list:\n" + "\n".join(
        f"{i}. {' '.join(w['words'])}" + (f" ≤ ${w['max']:g}" if w.get("max") else "") for i, w in enumerate(ws, 1))
        if ws else "🛒 Watch list is empty — /watch rtx 4090 1500")


async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return
    from services import deal_tracker as dt_
    try:
        n = int((context.args or ["0"])[0])
    except ValueError:
        n = 0
    ws = await dt_.remove(n)
    await _reply(update, f"🛒 Removed #{n}. {len(ws)} left.")


async def deals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return

    async def go():
        from services import deal_tracker as dt_
        r = await dt_.check(notify=False)
        if not r["hits"]:
            await _reply(update, f"🛒 No new matches ({r['watches']} watches, {r.get('items', 0)} deals scanned).")
            return
        await _reply(update, "🛒 New matches:\n" + "\n".join(f"• {h['title'][:110]}\n  {h['link']}" for h in r["hits"][:8]))
    await _guard(update, go)


HANDLERS.update({"watch": watch_cmd, "watches": watches_cmd, "unwatch": unwatch_cmd, "deals": deals_cmd})
OWNER_COMMANDS += [("watch", "Watch a deal: /watch rtx 4090 1500 (owner)"), ("deals", "Check deals now (owner)")]



# ── hardware scout (owner) ─────────────────────────────────────────────────────
def _num(x: str) -> Optional[float]:
    try:
        return float(x.replace(",", "").lstrip("$").lower().replace("kes", ""))
    except (ValueError, AttributeError):
        return None


async def _owner_only(update: Update) -> bool:
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return False
    return True


async def scoutwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from services import hardware_scout as hs
    args = list(context.args or [])
    mx = _num(args[-1]) if args and len(args) > 1 and _num(args[-1]) is not None else None
    words = [a.lower() for a in (args[:-1] if mx is not None else args)]
    if not words:
        await _reply(update, "Usage: /scoutwatch dell r740 600  (search words, optional max USD)")
        return
    v = await hs.load()
    v["watches"].append({"words": words, "max_usd": mx, "min_seller": 98})
    await hs.save(v)
    await _reply(update, f"🔧 Scouting eBay for: {' '.join(words)}" + (f" ≤ ${mx:g}" if mx else "") + " (seller ≥ 98 %)")


async def scoutlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from services import hardware_scout as hs
    v = await hs.load()
    w = "\n".join(f"{i}. {' '.join(x['words'])}" + (f" ≤ ${x['max_usd']:g}" if x.get("max_usd") else "")
                  for i, x in enumerate(v["watches"], 1)) or "— empty (/scoutwatch …)"
    p = "\n".join(f"• {k}: KES {kes:,.0f}" for k, kes in sorted(v["prices"].items())) or "— empty (/price …)"
    await _reply(update, f"🔧 Scout list\n{w}\n\n💵 Local selling prices\n{p}")


async def scoutdel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from services import hardware_scout as hs
    v = await hs.load()
    n = int(_num((context.args or ["0"])[0]) or 0)
    if 1 <= n <= len(v["watches"]):
        v["watches"].pop(n - 1)
        await hs.save(v)
    await _reply(update, f"🔧 {len(v['watches'])} searches left.")


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from services import hardware_scout as hs
    args = list(context.args or [])
    kes = _num(args[-1]) if args else None
    if kes is None or len(args) < 2:
        await _reply(update, "Usage: /price rtx 3090 145000  (what it sells for locally, KES)")
        return
    v = await hs.load()
    phrase = " ".join(a.lower() for a in args[:-1])
    v["prices"][phrase] = kes
    await hs.save(v)
    await _reply(update, f"💵 {phrase} sells for KES {kes:,.0f}")


async def scout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return

    async def go():
        from services import hardware_scout as hs
        r = await hs.scout(notify=False)
        if not r.get("ok"):
            await _reply(update, f"🔧 {r['reason']}")
            return
        if not r["opportunities"]:
            await _reply(update, f"🔧 Scanned {r['scanned']} listings — nothing above your margin yet.")
            return
        await _reply(update, hs.card(r["opportunities"][:6]))
    await _guard(update, go)


async def bought_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from services import hardware_scout as hs
    args = list(context.args or [])
    cost = _num(args[-1]) if args else None
    if cost is None or len(args) < 2:
        await _reply(update, "Usage: /bought rtx 3090 founders 98000  (description, landed cost KES)")
        return
    v = await hs.load()
    nid = max([x["id"] for x in v["ledger"]] or [0]) + 1
    v["ledger"].append({"id": nid, "desc": " ".join(args[:-1]), "cost_kes": cost, "bought": time.strftime("%Y-%m-%d")})
    await hs.save(v)
    await _reply(update, f"📦 #{nid} in stock: {' '.join(args[:-1])} · cost KES {cost:,.0f}")


async def sold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from services import hardware_scout as hs
    args = list(context.args or [])
    if len(args) < 2 or _num(args[0]) is None or _num(args[1]) is None:
        await _reply(update, "Usage: /sold 3 145000  (stock #, sale price KES)")
        return
    v = await hs.load()
    it = next((x for x in v["ledger"] if x["id"] == int(_num(args[0]))), None)
    if not it:
        await _reply(update, "No such stock number — see /stock")
        return
    it["sold_kes"], it["sold"] = _num(args[1]), time.strftime("%Y-%m-%d")
    await hs.save(v)
    await _reply(update, f"💰 #{it['id']} sold for KES {it['sold_kes']:,.0f} · profit KES {it['sold_kes'] - it['cost_kes']:,.0f}")


async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from services import hardware_scout as hs
    v = await hs.load()
    st = [x for x in v["ledger"] if not x.get("sold_kes")]
    await _reply(update, "📦 In stock\n" + "\n".join(f"#{x['id']} {x['desc']} · KES {x['cost_kes']:,.0f} · {x['bought']}"
                                                      for x in st) if st else "📦 No stock on hand.")


async def ledger_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    from decouple import config as _cfg
    from services import hardware_scout as hs
    v = await hs.load()
    t = hs.ledger_totals(v["ledger"], str(_cfg("SCOUT_VAT_REGISTERED", default="false")).lower() in ("1", "true", "yes"))
    await _reply(update, f"📒 Hardware ledger\n{t['sold']} sold · revenue KES {t['revenue_kes']:,.0f} · cost KES "
                         f"{t['cost_kes']:,.0f} · output VAT KES {t['output_vat_kes']:,.0f}\nProfit KES {t['profit_kes']:,.0f}"
                         f"\nStock: {t['in_stock']} items · KES {t['stock_value_kes']:,.0f}")


HANDLERS.update({"scoutwatch": scoutwatch_cmd, "scoutlist": scoutlist_cmd, "scoutdel": scoutdel_cmd,
                 "price": price_cmd, "scout": scout_cmd, "bought": bought_cmd, "sold": sold_cmd,
                 "stock": stock_cmd, "ledger": ledger_cmd})
OWNER_COMMANDS += [("scout", "Hardware scout now (owner)"), ("scoutlist", "Scout searches + prices (owner)"),
                   ("stock", "Hardware stock (owner)"), ("ledger", "Hardware profit + VAT (owner)")]


# ── copy trader (owner) ─────────────────────────────────────────────────────────
async def accounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return

    async def go():
        from services import copy_trade
        await _reply(update, copy_trade.text(await copy_trade.status()))
    await _guard(update, go)


async def _copy_ctl(update: Update, context: ContextTypes.DEFAULT_TYPE, **kw):
    if not is_owner(update):
        await _reply(update, "🔒 Owner only.")
        return
    args = context.args or []
    if not args:
        await _reply(update, "Usage: /copypause <login|all> · /copyresume <login|all> · /copymult <login> <0-10>")
        return

    async def go():
        from services import copy_trade
        if "mult" in kw:
            try:
                kw["mult"] = float(args[1])
            except (IndexError, ValueError):
                await _reply(update, "Usage: /copymult <login> <multiplier, e.g. 0.5>")
                return
        r = await copy_trade.control(args[0], **kw)
        await _reply(update, "✅ " + ", ".join(f"#{k}: {'paused' if v.get('paused') else 'copying'} x{v.get('mult', 1):g}"
                                              for k, v in r["accounts"].items()) if r["accounts"] else "No accounts yet.")
    await _guard(update, go)


async def copypause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _copy_ctl(update, context, paused=True)


async def copyresume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _copy_ctl(update, context, paused=False)


async def copymult_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _copy_ctl(update, context, mult=None)


HANDLERS.update({"accounts": accounts_cmd, "copypause": copypause_cmd, "copyresume": copyresume_cmd,
                 "copymult": copymult_cmd})
OWNER_COMMANDS += [("accounts", "Copy trader: master + follower accounts (owner)"),
                   ("copypause", "Pause copying: /copypause <login|all> (owner)"),
                   ("copyresume", "Resume copying: /copyresume <login|all> (owner)"),
                   ("copymult", "Lot multiplier: /copymult <login> 0.5 (owner)")]
