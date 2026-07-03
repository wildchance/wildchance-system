"""On-demand Telegram commands — pull EdgeFinder / setups / calendar reads.

  /edge  (or /board)     ranked EdgeFinder bias board
  /bias  EURUSD          deep single-pair read (factor breakdown)
  /setup EURUSD [side]   full Entry/SL/TP card via the setup engine
  /calendar [USD,EUR]    this week's high-impact events

All handlers are defensive: any error replies with a short message instead of
crashing the webhook.
"""

from telegram import Update
from telegram.ext import ContextTypes

from services import edgefinder_service
from services import signal_engine as se
from services.news_guard import high_impact_calendar

_ICON = {"STRONG LONG": "🟢🟢", "LONG": "🟢", "NEUTRAL": "➖",
         "SHORT": "🔴", "STRONG SHORT": "🔴🔴"}


async def handle_edge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ranked EdgeFinder bias board."""
    try:
        board = (await edgefinder_service.scoreboard()).get("board", [])
        if not board:
            await update.message.reply_text(
                "🧭 EdgeFinder: the feed is empty — run a wildchance scrape first.")
            return
        lines = ["🧭 *EdgeFinder — Bias Board*", ""]
        for r in board[:10]:
            news = " ⚠️" if r.get("news") else ""
            lines.append(f"{_ICON.get(r['bias'], '')} *{r['pair']}* {r['bias']} "
                         f"({r['score']:+d})  ·  sys {r.get('system_verdict') or '—'}{news}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"EdgeFinder error: {e}")


async def handle_bias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/bias EURUSD — one pair's full factor breakdown."""
    if not context.args:
        await update.message.reply_text("Usage: /bias EURUSD")
        return
    symbol = context.args[0]
    try:
        row = await edgefinder_service.pair_read(symbol)
        if not row:
            await update.message.reply_text(f"{symbol}: not in the watchlist / feed.")
            return
        lines = [
            f"{_ICON.get(row['bias'], '')} *{row['pair']}* — {row['bias']} ({row['score']:+d})",
            f"system {row.get('system_verdict') or '—'} · conf {row.get('system_confidence')}%",
            "",
        ]
        for f in row.get("factors", []):
            lines.append(f"• {f['name']}: {f['score']:+d} — {f['note']}")
        if row.get("news"):
            lines += ["", row["news"]]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"bias error: {e}")


async def handle_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setup EURUSD [long|short] — full Entry/SL/TP card."""
    if not context.args:
        await update.message.reply_text("Usage: /setup EURUSD [long|short]")
        return
    symbol = context.args[0]
    side = context.args[1].lower() if len(context.args) > 1 else "long"
    if side not in ("long", "short"):
        side = "long"
    try:
        sig = await se.build_auto(symbol, side)
        await update.message.reply_text(se.format_card(sig), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"setup error: {e}")


async def handle_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/calendar [USD,EUR] — high-impact events this week."""
    ccys = None
    if context.args:
        ccys = {c.strip().upper() for c in context.args[0].split(",") if c.strip()}
    try:
        events = await high_impact_calendar(ccys)
        if not events:
            await update.message.reply_text("🗓️ No high-impact events found this week.")
            return
        lines = ["🗓️ *High-impact calendar*", ""]
        for e in events[:15]:
            act = f"  →  {e['actual']}" if e.get("actual") else ""
            lines.append(f"{e.get('date')}  {e['ccy']}  {e['event']}{act}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"calendar error: {e}")
