import os
import sqlite3
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")        # No pongas el token aquí directamente
STAFF_CHAT_ID = int(os.environ.get("STAFF_CHAT_ID", "0"))
ADMINS = list(map(int, os.environ.get("ADMINS", "").split(",")))
API_SECRET = os.environ.get("API_SECRET", "")
API_URL = "https://tumanzanita.store/api/agregar_saldo"

# Base de datos local
conn = sqlite3.connect("transactions.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    telegram_id INTEGER,
    metodo TEXT,
    monto REAL,
    estado TEXT,
    foto_id TEXT,
    created_at TEXT
)""")
conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    args = context.args
    if args:
        metodo, monto = args[0].split("_")
        await update.message.reply_text(
            f"Has elegido *{metodo}*.\nEnvía el pago de *S/{monto}* al número **999999999** o escanea este QR:",
            parse_mode="Markdown"
        )
        await update.message.reply_photo(open("qr_yape.png", "rb"))
        await update.message.reply_text("Cuando termines, envíame aquí la captura 📸 del pago.")
    else:
        await update.message.reply_text("Hola 👋. Por favor inicia desde tu panel web para recargar fondos.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    photo = update.message.photo[-1].file_id

    cur.execute("INSERT INTO transactions (username, telegram_id, metodo, monto, estado, foto_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user.username, user.id, "YAPE", 0, "pendiente", photo, datetime.now().isoformat()))
    conn.commit()

    await context.bot.send_photo(
        chat_id=STAFF_CHAT_ID,
        photo=photo,
        caption=f"📸 Captura recibida de @{user.username} (ID: {user.id})\nUsa /ok @{user.username} monto"
    )
    await update.message.reply_text("✅ Recibido. Espera la validación del equipo.")

async def validar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMINS:
        await update.message.reply_text("No tienes permiso para validar.")
        return

    try:
        username = context.args[0].replace("@", "")
        monto = float(context.args[1])

        headers = {"X-SECRET-KEY": API_SECRET}
        payload = {"username": username, "monto": monto, "metodo": "YAPE"}
        requests.post(API_URL, json=payload, headers=headers)

        await update.message.reply_text(f"✅ Saldo acreditado a @{username}.")
        # await context.bot.send_message(chat_id=f"@{username}", text=f"🎉 Tu recarga de S/{monto} ha sido validada correctamente.")
    except Exception as e:
        await update.message.reply_text(f"Error al procesar: {e}")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(CommandHandler("ok", validar))
app.run_polling()

