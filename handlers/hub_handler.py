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
                  "🧾 /audit — data audit", "", "_Flash cards, backups and daily reports arrive here as files._"]
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
