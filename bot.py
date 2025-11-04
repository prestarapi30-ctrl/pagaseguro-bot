import os
import logging
from dotenv import load_dotenv
import requests
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from db import init_db, get_conn, upsert_telegram_link, set_pending_intent, get_pending_intent, add_transaction

# ----------------------------
# Load environment
# ----------------------------
load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
API_SECRET = os.environ.get("API_SECRET", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:5001")
ADMINS = [u.strip() for u in os.environ.get("ADMINS", "").split(",") if u.strip()]
STAFF_CHAT_ID = os.environ.get("STAFF_CHAT_ID", "")

# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# ----------------------------
# Handlers
# ----------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command"""
    args = context.args
    method, amount = None, None
    if args:
        try:
            parts = args[0].split("_")
            method = parts[0].upper()
            amount = float(parts[1]) if len(parts) > 1 else None
        except Exception:
            method, amount = None, None

    chat_id = update.effective_chat.id
    tg_username = update.effective_user.username

    # Save telegram link
    upsert_telegram_link(chat_id=str(chat_id), telegram_username=tg_username, bound_username=tg_username)

    # Save pending intent if method+amount provided
    if method and amount and amount > 0:
        set_pending_intent(chat_id=str(chat_id), method=method, amount=amount)

    msg = [
        "👋 Bienvenido al bot de recargas.",
        "",
        "Pasos:",
        "1) Envía una foto del comprobante del pago aquí.",
        "2) Un admin validará y acreditará tu saldo."
    ]
    if method and amount:
        msg.append(f"\nIntento registrado: {method} {amount}")

    await update.message.reply_text("\n".join(msg))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment proof photos"""
    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        await update.message.reply_text("⚠️ No se recibió ninguna foto.")
        return

    file_id = photo.file_id
    chat_id = update.effective_chat.id

    # Lookup pending intent
    intent = get_pending_intent(str(chat_id))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT bound_username, telegram_username FROM telegram_links WHERE chat_id=?", (str(chat_id),))
    link = cur.fetchone()
    bound_username = link["bound_username"] if link else None
    tg_username = link["telegram_username"] if link else None
    conn.close()

    method = intent["method"] if intent else "DESCONOCIDO"
    amount = intent["amount"] if intent else 0
    username_for_tx = bound_username or tg_username or "desconocido"

    add_transaction(username_for_tx, float(amount), method, status="proof_submitted", proof_file_id=file_id)
    await update.message.reply_text("✅ Comprobante registrado. Un admin lo revisará pronto.")

    # Notify staff chat
    if STAFF_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(STAFF_CHAT_ID),
                text=f"📸 Comprobante recibido de @{tg_username or 'usuario'}: {method} {amount}. file_id={file_id}"
            )
        except Exception as e:
            logger.error(f"Error notificando staff: {e}")


async def ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to credit user"""
    caller = update.effective_user.username or ""
    if caller not in ADMINS:
        await update.message.reply_text("❌ No autorizado.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Uso: /ok @usuario monto")
        return

    username = context.args[0].lstrip("@")
    try:
        monto = float(context.args[1])
    except Exception:
        await update.message.reply_text("Monto inválido.")
        return

    # Call API to credit balance
    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/agregar_saldo",
            json={"username": username, "monto": monto, "metodo": "ADMIN"},
            headers={"X-SECRET-KEY": API_SECRET},
            timeout=10
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            await update.message.reply_text(f"✅ Saldo acreditado a {username}: {monto}")
        else:
            await update.message.reply_text(f"❌ Error al acreditar: {data}")
            return
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error de API: {e}")
        return

    # Notify user if chat_id known
    chat_id = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM telegram_links WHERE bound_username=? OR telegram_username=?", (username, username))
        row = cur.fetchone()
        if row and row["chat_id"]:
            chat_id = int(row["chat_id"])
        conn.close()
    except Exception as e:
        logger.error(f"No se pudo obtener chat_id del usuario {username}: {e}")

    if chat_id:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"🎉 Tu saldo ha sido acreditado: {monto}")
        except Exception:
            pass


# ----------------------------
# Main
# ----------------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN no configurado")
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CommandHandler("ok", ok))

    logger.info("Bot iniciado correctamente")
    app.run_polling()


if __name__ == "__main__":
    main()
