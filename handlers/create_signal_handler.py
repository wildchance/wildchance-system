"""/create_signal — build a trade signal (auto CBDR zones or fully manual) + QR.

Usage:
  AUTO  (system computes Entry/SL/TP1-3 from the CBDR setup engine):
    /create_signal EURUSD long auto
    /create_signal XAUUSD short auto prelondon 2500 6

  MANUAL (you type every price, SimpleSignals-style):
    /create_signal EURUSD long 1.0850 1.0820 1.0900 1.0950 1.1000
      → pair side ENTRY SL TP1 [TP2] [TP3]

Sends a signal card + a QR reference photo, and mirrors it to WhatsApp.
"""

from __future__ import annotations

from io import BytesIO

from telegram import Update, InputFile
from telegram.ext import ContextTypes

from services import signal_engine as se
from services.whatsapp_service import send_text as wa_send

USAGE = (
    "Usage:\n"
    "• Auto: /create_signal EURUSD long auto [window] [balance] [tier]\n"
    "• Manual: /create_signal EURUSD long ENTRY SL TP1 [TP2] [TP3]"
)


async def handle_create_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(USAGE)
        return

    symbol = args[0].upper()
    side = args[1].lower()
    if side not in ("long", "short", "buy", "sell"):
        await update.message.reply_text("Direction must be long/short.\n" + USAGE)
        return
    side = "long" if side in ("long", "buy") else "short"

    try:
        if args[2].lower() == "auto":
            window = args[3] if len(args) > 3 else "cbdr"
            balance = float(args[4]) if len(args) > 4 else 2500.0
            tier = args[5] if len(args) > 5 else "6"
            sig = await se.build_auto(symbol, side, window=window,
                                      balance=balance, tier=tier)
        else:
            prices = [float(x) for x in args[2:7]]
            if len(prices) < 3:
                await update.message.reply_text("Manual needs ENTRY SL TP1.\n" + USAGE)
                return
            entry, sl, tps = prices[0], prices[1], prices[2:]
            sig = se.build_manual(symbol, side, entry, sl, tps)
    except ValueError:
        await update.message.reply_text("Could not parse prices.\n" + USAGE)
        return

    if sig.get("error"):
        await update.message.reply_text(f"⚠️ {sig['error']}")
        return

    card = se.format_card(sig)
    png = se.make_qr(se.qr_payload(sig))
    if png:
        await update.message.reply_photo(
            photo=InputFile(BytesIO(png), filename="signal.png"),
            caption=card, parse_mode="Markdown")
    else:
        await update.message.reply_text(card, parse_mode="Markdown")

    # Mirror to WhatsApp (no-op if not configured).
    try:
        await wa_send(card.replace("*", "").replace("`", ""))
    except Exception:
        pass
