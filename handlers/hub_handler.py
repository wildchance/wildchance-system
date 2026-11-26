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
                   ("edge", "EdgeFinder: top setups −10…+10"), ("terminal", "Open the terminal"), ("help", "Show menu")]
OWNER_COMMANDS = [("open", "Open signals + exposure (owner)"), ("health", "Errors today (owner)"),
                  ("audit", "Data audit (owner)")]


def owner_ids() -> set:
    """OWNER_CHAT_ID may be one id or several (comma / space separated); quotes and
    spaces pasted into Render are ignored."""
    raw = OWNER_CHAT_ID.replace('"', "").replace("'", "")
    return {x.strip() for x in raw.replace(",", " ").split() if x.strip()}


def is_owner(update: Update) -> bool:
    """The owner's own user id OR the owner's chat id (private chat ids equal user ids)."""
    ids = owner_ids()
    if not ids:
        return False
    chat = str(update.effective_chat.id) if update.effective_chat else ""
    eu = getattr(update, "effective_user", None)
    user = str(eu.id) if eu else ""
    return chat in ids or user in ids


def denied_text(update: Update) -> str:
    chat = update.effective_chat.id if update.effective_chat else "?"
    eu = getattr(update, "effective_user", None)
    user = eu.id if eu else "?"
    # setting names in `code` so Markdown does not turn OWNER_CHAT_ID's underscores into italics
    if not owner_ids():
        return (f"🔒 Owner only — `OWNER_CHAT_ID` is not set on this server. Your user id is `{user}`: "
                "Render → wildchance-system → Environment → add `OWNER_CHAT_ID` = that number → Save "
                "(it redeploys).")
    return (f"🔒 Owner only. This chat id: `{chat}` · your user id: `{user}`. "
            "If this is you, set `OWNER_CHAT_ID` in Render to your user id (no spaces or quotes) and redeploy.")


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
             "💳 /subscribe — plans, pay with Telegram Stars · 🎁 /trial — free 7-day trial",
             "💱 /carry — carry scanner (who earns swap)",
             "🧱 /walls EURUSD — dealer walls (CME or vol screens)",
             "🗓 /calendar [USD,EUR] — high-impact events, next 7 days",
             "📈 /results — track record (wins · losses · breakevens · pips)",
             "🗒 /recent — last 10 closed signals",
             "🧭 /edge — EdgeFinder top setups (macro + sentiment + trend, −10…+10)",
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
        from services import edge_v2
        b = await edge_v2.cached()
        if b and b.get("rows"):
            await _reply(update, edge_v2.text(b))
            return
        import asyncio
        asyncio.get_running_loop().create_task(edge_v2.board(force=True))     # build now, in the background
        await _reply(update, "🧭 EdgeFinder board is being built now (2–5 min) — send /edge again shortly. "
                             "Meanwhile, the COT + retail read:")
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
        await _reply(update, denied_text(update))
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
        await _reply(update, denied_text(update))
        return
    from services import errors
    s = errors.summary()
    lines = [f"🩺 *Health today*: {s['total']} problem events"]
    lines += [f"• {t['kind']} × {t['n']} — {t['where']}" for t in s["top"][:10]]
    await _reply(update, "\n".join(lines) if s["total"] else "🩺 Health today: no crashes, failed jobs or slow calls. 👍")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, denied_text(update))
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
    for oid in owner_ids():
        try:
            await bot.set_my_commands([BotCommand(c, d) for c, d in PUBLIC_COMMANDS + OWNER_COMMANDS],
                                      scope=BotCommandScopeChat(int(oid)))
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
        if name.lower().endswith((".txt", ".csv")):           # a long Forex Factory paste saved as a file
            from services import calendar_paste as cp
            text = blob.decode("utf-8", "ignore")
            if cp.looks_like_ff(text):
                await update.message.reply_text(await _ff_paste_reply(text))
                return
        rep = await cme_ingest.ingest([(name, blob)])
        await update.message.reply_text(cme_ingest.reply_text(rep))
    await _guard(update, go)


async def _ff_paste_reply(text: str) -> str:
    """Store a Forex Factory paste, fold its actuals into the macro layer, and say what changed."""
    from services import calendar_paste as cp, macro_surprise as ms
    ev = cp.parse(text)
    r = await cp.store(ev)
    m = await ms.ingest(extra=ev)
    printed = sum(1 for e in ev if e.get("actual"))
    return (f"🗓 Calendar paste: {r['added']} releases stored ({r['high']} high impact, {printed} with actuals) — "
            f"blackouts and rotation use them now. Macro layer: {m['new_prints']} new prints → /edge. "
            "Long history? Save it as a .txt file and send the file.")


async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner pastes the Forex Factory week → stored as calendar events; anything else → /help."""
    if not _private(update):
        return                                           # never chat back to every group message
    text = update.message.text or ""
    from services import treasury
    if treasury.looks_secret(text):                       # a seed phrase / private key pasted by mistake
        await update.message.reply_text(
            "⚠️ That looks like a SEED PHRASE or PRIVATE KEY. Delete your message now. It was not stored. "
            "The bot only ever needs PUBLIC addresses. If this was a real wallet secret, move the funds "
            "to a new wallet.")
        return
    from services import calendar_paste as cp
    if is_owner(update) and cp.looks_like_ff(text):
        async def go():
            await _reply(update, await _ff_paste_reply(text))
        await _guard(update, go)
        return
    await update.message.reply_text("Type /help for the menu.")


HANDLERS["chatid"] = chatid_cmd


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, denied_text(update))
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
        await _reply(update, denied_text(update))
        return
    from services import deal_tracker as dt_
    ws = (await dt_._load())["watches"]
    await _reply(update, "🛒 Watch list:\n" + "\n".join(
        f"{i}. {' '.join(w['words'])}" + (f" ≤ ${w['max']:g}" if w.get("max") else "") for i, w in enumerate(ws, 1))
        if ws else "🛒 Watch list is empty — /watch rtx 4090 1500")


async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, denied_text(update))
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
        await _reply(update, denied_text(update))
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
        await _reply(update, denied_text(update))
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
        await _reply(update, denied_text(update))
        return

    async def go():
        from services import copy_trade
        await _reply(update, copy_trade.text(await copy_trade.status()))
    await _guard(update, go)


async def _copy_ctl(update: Update, context: ContextTypes.DEFAULT_TYPE, **kw):
    if not is_owner(update):
        await _reply(update, denied_text(update))
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


async def flashtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/flashtest [EURUSD] [BUY open close] → the latest TP-hit flash card, to the owner's chat only."""
    if not is_owner(update):
        await _reply(update, denied_text(update))
        return
    args = context.args or []
    sym = args[0].upper() if args else "EURUSD"
    if len(sym) == 6 and sym.isalpha():
        sym = f"{sym[:3]}/{sym[3:]}"
    side = args[1].upper() if len(args) > 1 else None
    try:
        op = float(args[2]) if len(args) > 2 else None
        cl = float(args[3]) if len(args) > 3 else None
    except ValueError:
        await _reply(update, "Usage: /flashtest EURUSD  ·  or  /flashtest EURUSD SELL 1.1650 1.1610")
        return

    async def go():
        from services import flash_card as fc
        r = await fc.send_test(sym, side, op, cl)
        if not r.get("sent"):
            await _reply(update, f"⚠️ Flash test: {r.get('reason') or r}")
    await _guard(update, go)


HANDLERS["flashtest"] = flashtest_cmd
OWNER_COMMANDS += [("flashtest", "Test flash card to this chat: /flashtest EURUSD (owner)")]



# ── watch-only treasury (owner) ─────────────────────────────────────────────────
async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, denied_text(update))
        return

    async def go():
        from services import treasury
        await _reply(update, treasury.text(await treasury.snapshot()))
    await _guard(update, go)


async def walletadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, denied_text(update))
        return
    from services import treasury
    args = context.args or []
    if treasury.looks_secret(" ".join(args)) or any(treasury.looks_secret(a) for a in args):
        await _reply(update, "⚠️ That is a secret, not a public address. Delete the message; nothing was stored.")
        return
    if len(args) < 2:
        await _reply(update, "Usage: /walletadd <btc|eth|tron|ton> <PUBLIC address> [label]\n"
                             "e.g. /walletadd tron TXyz… Tangem USDT")
        return

    async def go():
        r = await treasury.add(args[0], args[1], " ".join(args[2:]))
        await _reply(update, f"✅ Watching #{r['n']} — /wallet to see balances" if r["ok"] else f"⚠️ {r['reason']}")
    await _guard(update, go)


async def walletdel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await _reply(update, denied_text(update))
        return
    try:
        n = int((context.args or ["0"])[0])
    except ValueError:
        n = 0

    async def go():
        from services import treasury
        r = await treasury.remove(n)
        await _reply(update, f"🗑 Stopped watching {r['removed']['label']}" if r["ok"] else f"⚠️ {r['reason']}")
    await _guard(update, go)


# ── subscriptions: Telegram Stars (+ optional Crypto Pay link) ──────────────────
async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from services import payments
    args = context.args or []
    if len(args) < 2:
        await _reply(update, payments.plans_text())
        return
    tier, email = args[0].lower(), args[1].lower()
    try:
        months = max(1, min(12, int(args[2]))) if len(args) > 2 else 1
    except ValueError:
        months = 1
    stars = payments.stars_for(tier, months)
    if not stars or not payments.EMAIL.match(email):
        await _reply(update, "⚠️ Unknown plan or email.\n" + payments.plans_text())
        return

    async def go():
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id, title=f"WildChance {tier.title()}",
            description=f"{months} month(s) of {tier.title()} for {email}. Access opens as soon as it is paid.",
            payload=payments.payload(tier, months, email), provider_token="", currency="XTR",
            prices=[LabeledPrice(f"{tier.title()} × {months} mo", stars)])
        if payments.CRYPTOPAY_IN_BOT and payments.CRYPTOPAY_TOKEN:
            inv = await payments.cryptopay_invoice(tier, months, email)
            if inv.get("ok"):
                await update.message.reply_text(
                    "Or pay with crypto (USDT / TON / BTC):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Pay with crypto", url=inv["url"])]]))
    await _guard(update, go)


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram asks before charging: confirm the order is still valid."""
    from services import payments
    q = update.pre_checkout_query
    ok = q.currency == "XTR" and payments.parse_payload(q.invoice_payload) is not None
    await q.answer(ok=ok, error_message=None if ok else "This order is no longer valid — send /subscribe again.")


async def paid_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from services import payments
    sp = update.message.successful_payment
    p = payments.parse_payload(sp.invoice_payload)
    if not p:
        await update.message.reply_text("⚠️ Payment received but the order was unreadable — /paysupport and we fix it.")
        return
    r = await payments.apply_once(sp.telegram_payment_charge_id, p["tier"], p["months"], p["email"], "stars")
    if r.get("ok"):
        await update.message.reply_text(
            f"✅ Paid — {p['tier'].title()} is active for {p['email']} until {str(r.get('paid_until', ''))[:10]}. "
            "Sign in on the members page with that email. Thank you!")
    else:
        await update.message.reply_text(f"⚠️ Payment received but activation failed ({r.get('reason')}). "
                                        "The owner has been told — /paysupport.")


async def paysupport_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram requires bots that take payments to answer /paysupport."""
    await _reply(update, "💬 Payment help: reply here with your email and what happened. Refunds for Stars "
                         "payments are handled by the owner within 48 h.")
    try:
        from services.owner_notify import send
        u = update.effective_user
        await send(f"💬 /paysupport from {getattr(u, 'username', None) or getattr(u, 'id', '?')} "
                   f"(user id {getattr(u, 'id', '?')})")
    except Exception:
        pass


async def refund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/refund <user_id> <telegram_payment_charge_id> — refund a Stars payment (owner)."""
    if not is_owner(update):
        await _reply(update, denied_text(update))
        return
    args = context.args or []
    if len(args) < 2:
        await _reply(update, "Usage: /refund <user_id> <charge_id> (both are in the payment notice)")
        return

    async def go():
        ok = await context.bot.refund_star_payment(user_id=int(args[0]), telegram_payment_charge_id=args[1])
        await _reply(update, "↩️ Refunded." if ok else "⚠️ Refund failed.")
    await _guard(update, go)


HANDLERS.update({"wallet": wallet_cmd, "walletadd": walletadd_cmd, "walletdel": walletdel_cmd,
                 "subscribe": subscribe_cmd, "plans": subscribe_cmd, "paysupport": paysupport_cmd,
                 "refund": refund_cmd})
PUBLIC_COMMANDS += [("subscribe", "Plans + pay with Telegram Stars"), ("paysupport", "Payment help")]
OWNER_COMMANDS += [("wallet", "Treasury balances (watch-only, owner)"),
                   ("walletadd", "Watch a public address: /walletadd tron T… (owner)"),
                   ("walletdel", "Stop watching: /walletdel 2 (owner)"),
                   ("refund", "Refund a Stars payment (owner)")]



# ── EdgeFinder v2 extras + free trial ───────────────────────────────────────────
async def carry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def go():
        from services import edge_v2
        b = await edge_v2.cached()
        if not b or not b.get("rows"):                     # carry needs no prices: answer from the rate table
            from services.free_macro_feeds import POLICY_RATES
            b = {"rows": [{"symbol": sym, "carry": edge_v2.carry(sym, dict(POLICY_RATES))} for sym in edge_v2.FX]}
        await _reply(update, edge_v2.carry_text(b))
    await _guard(update, go)


TRIAL_DAYS = int(config("TRIAL_DAYS", default="7"))
TRIAL_TIER = str(config("TRIAL_TIER", default="trader"))


async def trial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/trial you@email.com → TRIAL_DAYS of TRIAL_TIER, once per Telegram account and per email."""
    from services import payments
    args = context.args or []
    if not args or not payments.EMAIL.match(args[0]):
        await _reply(update, f"🎁 Free {TRIAL_DAYS}-day {TRIAL_TIER.title()} trial: /trial you@email.com\n"
                             "One trial per person. No payment details needed.")
        return
    email = args[0].lower()
    eu = getattr(update, "effective_user", None)
    uid = str(eu.id) if eu else str(update.effective_chat.id)

    async def go():
        from services import data_store, billing_service as bs
        from database.db import AsyncSessionLocal
        used, _ = await data_store.get("trials")
        used = dict(used or {})
        users, emails = set(used.get("users", [])), set(used.get("emails", []))
        if uid in users or email in emails:
            await _reply(update, "🎁 A trial was already used for this Telegram account or email. "
                                 "/subscribe to continue — thank you for trying WildChance!")
            return
        async with AsyncSessionLocal() as db:
            r = await bs.start_trial(db, email, TRIAL_TIER, TRIAL_DAYS, f"tg:{uid}")
        if not r.get("ok"):
            await _reply(update, f"⚠️ {r.get('reason')}")
            return
        used["users"], used["emails"] = sorted(users | {uid}), sorted(emails | {email})
        await data_store.put("trials", used)
        await _reply(update, f"🎁 Trial on: {TRIAL_TIER.title()} for {email} until {r['paid_until'][:10]}. "
                             "Sign in on the members page with that email. /edge and /terminal are yours to explore.")
        try:
            from services.owner_notify import send
            await send(f"🎁 New trial: {email} (tg {uid}) → {TRIAL_TIER} {TRIAL_DAYS} d")
        except Exception:
            pass
    await _guard(update, go)


HANDLERS.update({"carry": carry_cmd, "trial": trial_cmd})
PUBLIC_COMMANDS += [("carry", "Carry scanner: who earns swap"), ("trial", f"Free {TRIAL_DAYS}-day trial")]



LEGACY_COMMANDS = {"admin", "create_signal", "bias", "setup"}     # registered in routes/telegram_routes.py


def split_glued(text: str, known) -> Optional[tuple]:
    """'/trialme@mail.com' → ('trial', ['me@mail.com']): Telegram makes a command out of everything
    up to the first space, so a missing space hides the argument inside the command name."""
    head, *rest = (text or "").split()
    name = head[1:].split("@")[0].lower() if head.startswith("/") else ""
    raw = head[1:]
    if name in known:
        return None
    for k in sorted(known, key=len, reverse=True):
        if name != k and raw.lower().startswith(k) and len(raw) > len(k):
            return k, [raw[len(k):]] + rest
    return None


async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Last-resort handler: recover glued arguments, else point to /help (never stay silent)."""
    text = (update.message.text if update.message else "") or ""
    name = text.split()[0][1:].split("@")[0].lower() if text.startswith("/") else ""
    if name in HANDLERS or name in LEGACY_COMMANDS:
        return                                            # a real command: its own handler answered
    hit = split_glued(text, HANDLERS)
    if hit:
        context.args = hit[1]
        await HANDLERS[hit[0]](update, context)
        return
    if _private(update):
        await update.message.reply_text(f"🤔 I don't know {text.split()[0][:40]} — type /help for the menu "
                                        "(put a space before any email or symbol, e.g. /trial you@email.com).")
