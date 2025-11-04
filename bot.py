import os
import sqlite3
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ----------------------------
# Variables desde Render
# ----------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
STAFF_CHAT_ID = int(os.environ.get("STAFF_CHAT_ID", "0"))
ADMINS = [int(x) for x in os.environ.get("ADMINS", "").split(",") if x]
API_SECRET = os.environ.get("API_SECRET", "")
API_URL = "https://<tu-servicio>.onrender.com/api/agregar_saldo"  # Reemplaza con tu URL real

# ----------------------------
# Base de datos local
# ----------------------------
conn = sqlite3.connect("transactions.db", check_same_thread=False)
cur = conn.cursor()

# Tabla de transacciones
cur.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    telegram_id INTEGER,
    metodo TEXT,
    monto REAL,
    estado TEXT,
    foto_id TEXT,
    created_at TEXT
)
""")

# Tabla de usuarios del bot (para guardar chat_id)
cur.execute("""
CREATE TABLE IF NOT EXISTS usuarios_bot (
    username TEXT PRIMARY KEY,
    telegram_id INTEGER
)
""")
conn.commit()

# ----------------------------
# Comandos del bot
# ----------------------------

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    args = context.args

    # Guardar chat_id del usuario (solo una vez)
    cur.execute("INSERT OR IGNORE INTO usuarios_bot (username, telegram_id) VALUES (?, ?)", (user.username, user.id))
    conn.commit()

    if args:
        try:
            metodo, monto = args[0].split("_")
        except:
            await update.message.reply_text("Formato incorrecto. Usa /start yape_20 por ejemplo.")
            return

        await update.message.reply_text(
            f"Has elegido *{metodo.upper()}*.\nEnvía el pago de *S/{monto}* al número **999999999** o escanea este QR:",
            parse_mode="Markdown"
        )
        try:
            await update.message.reply_photo(open("qr_yape.png", "rb"))
        except:
            await update.message.reply_text("⚠️ No se encontró la imagen qr_yape.png.")
        await update.message.reply_text("Cuando termines, envíame aquí la captura 📸 del pago.")
    else:
        await update.message.reply_text("Hola 👋. Por favor inicia desde tu panel web para recargar fondos.")

# Manejo de fotos de pago
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    photo = update.message.photo[-1].file_id

    cur.execute("""
    INSERT INTO transactions (username, telegram_id, metodo, monto, estado, foto_id, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user.username, user.id, "YAPE", 0, "pendiente", photo, datetime.now().isoformat()))
    conn.commit()

    # Enviar captura al staff
    await context.bot.send_photo(
        chat_id=STAFF_CHAT_ID,
        photo=photo,
        caption=f"📸 Captura recibida de @{user.username} (ID: {user.id})\nUsa /ok @{user.username} monto"
    )
    await update.message.reply_text("✅ Recibido. Espera la validación del equipo.")

# /ok para validar pagos (solo admins)
async def validar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMINS:
        await update.message.reply_text("No tienes permiso para validar.")
        return

    try:
        username = context.args[0].replace("@", "")
        monto = float(context.args[1])

        # Buscar chat_id del usuario
        cur.execute("SELECT telegram_id FROM usuarios_bot WHERE username = ?", (username,))
        result = cur.fetchone()
        chat_id = result[0] if result else None

        # Llamada a la API para acreditar saldo
        payload = {"username": username, "monto": monto, "metodo": "YAPE"}
        headers = {"X-SECRET-KEY": API_SECRET}
        response = requests.post(API_URL, json=payload, headers=headers)
        if response.status_code != 200:
            await update.message.reply_text(f"Error al acreditar saldo: {response.text}")
            return

        # Confirmación al admin
        await update.message.reply_text(f"✅ Saldo acreditado a @{username}.")

        # Mensaje al usuario
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=f"🎉 Tu recarga de S/{monto} ha sido validada correctamente.")
        else:
            await update.message.reply_text("⚠️ No se pudo enviar mensaje al usuario (no se encontró chat_id).")

    except Exception as e:
        await update.message.reply_text(f"Error al procesar: {e}")

# ----------------------------
# Inicialización del bot
# ----------------------------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(CommandHandler("ok", validar))

app.run_polling()
