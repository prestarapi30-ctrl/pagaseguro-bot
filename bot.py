import os
import requests
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from db import init_db, get_conn, upsert_telegram_link, set_pending_intent, get_pending_intent, add_transaction


BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_SECRET = os.environ.get("API_SECRET", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:5001")
ADMINS = [u.strip() for u in os.environ.get("ADMINS", "").split(',') if u.strip()]
STAFF_CHAT_ID = os.environ.get("STAFF_CHAT_ID", "")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    method = None
    amount = None
    if args:
        try:
            token = args[0]
            parts = token.split('_')
            method = parts[0].upper()
            amount = float(parts[1]) if len(parts) > 1 else None
        except Exception:
            method, amount = None, None

    chat_id = update.effective_chat.id
    tg_username = update.effective_user.username  # may be None

    # Store link
    # Bind to same username if the site username equals their Telegram @username
    upsert_telegram_link(chat_id=str(chat_id), telegram_username=tg_username, bound_username=tg_username)

    # If we have a method intent, store it for this chat
    if method and amount and amount > 0:
        set_pending_intent(chat_id=str(chat_id), method=method, amount=amount)

    msg = [
        "Bienvenido al bot de recargas de SERVIS.",
        "",
        "Pasos:",
        "1) Envía una foto del comprobante del pago aquí.",
        "2) Un admin validará y acreditará tu saldo.",
    ]
    if method and amount:
        msg.append("")
        msg.append(f"Intento registrado: {method} por {amount}.")

    await update.message.reply_text("\n".join(msg))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1] if update.message.photo else None
    file_id = photo.file_id if photo else None
    chat_id = update.effective_chat.id

    # Look up pending intent and bound username
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
    username_for_tx = bound_username or (tg_username or "desconocido")

    add_transaction(username_for_tx, float(amount), method, status="proof_submitted", proof_file_id=file_id)
    await update.message.reply_text("Comprobante registrado. Un admin lo revisará pronto.")

    # Notify staff chat if configured
    if STAFF_CHAT_ID:
        try:
            text = f"Comprobante recibido de @{tg_username or 'usuario'}: {method} {amount}. file_id={file_id}"
            await context.bot.send_message(chat_id=int(STAFF_CHAT_ID), text=text)
        except Exception:
            pass


async def ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only allow admin usernames
    caller = update.effective_user.username or ""
    if caller not in ADMINS:
        await update.message.reply_text("No autorizado.")
        return
    # Expect: /ok @usuario monto
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /ok @usuario monto")
        return
    user_arg = context.args[0]
    monto_arg = context.args[1]
    username = user_arg.lstrip('@')
    try:
        monto = float(monto_arg)
    except Exception:
        await update.message.reply_text("Monto inválido.")
        return

    # Call API to credit balance
    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/agregar_saldo",
            json={"username": username, "monto": monto, "metodo": "ADMIN"},
            headers={"X-SECRET-KEY": API_SECRET},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            await update.message.reply_text(f"Saldo acreditado a {username}: {monto}")
        else:
            await update.message.reply_text(f"Error al acreditar: {data}")
            return
    except Exception as e:
        await update.message.reply_text(f"Error de API: {e}")
        return

    # Notify target user if we know chat_id from links or users
    chat_id = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if row and row["chat_id"]:
            chat_id = int(row["chat_id"])
        else:
            cur.execute("SELECT chat_id FROM telegram_links WHERE bound_username=? OR telegram_username=?", (username, username))
            row2 = cur.fetchone()
            if row2 and row2["chat_id"]:
                chat_id = int(row2["chat_id"])
        conn.close()
    except Exception:
        chat_id = None

    if chat_id:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"Tu saldo ha sido acreditado: {monto}")
        except Exception:
            pass


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN no configurado")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CommandHandler("ok", ok))
    app.run_polling()


if __name__ == "__main__":
    main()
