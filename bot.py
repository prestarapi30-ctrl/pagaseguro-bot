import os
from dotenv import load_dotenv
import requests
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import sqlite3

# ----------------------------
# Cargar variables de entorno
# ----------------------------
load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_SECRET = os.environ.get("API_SECRET", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:5001")
ADMINS = [u.strip() for u in os.environ.get("ADMINS", "").split(',') if u.strip()]
STAFF_CHAT_ID = os.environ.get("STAFF_CHAT_ID", None)

# ----------------------------
# Base de datos SQLite
# ----------------------------
conn = sqlite3.connect("bot_users.db", check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Tabla de enlaces Telegram-usuario
cur.execute("""
CREATE TABLE IF NOT EXISTS telegram_links (
    chat_id TEXT PRIMARY KEY,
    telegram_username TEXT,
    bound_username TEXT
)
""")

# Tabla de transacciones
cur.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    method TEXT,
    amount REAL,
    status TEXT,
    proof_file_id TEXT,
    created_at TEXT
)
""")
conn.commit()

# ----------------------------
# Funciones de DB
# ----------------------------
def upsert_telegram_link(chat_id, telegram_username, bound_username):
    cur.execute("""
    INSERT INTO telegram_links (chat_id, telegram_username, bound_username)
    VALUES (?,?,?)
    ON CONFLICT(chat_id) DO UPDATE SET telegram_username=excluded.telegram_username, bound_username=excluded.bound_username
    """, (chat_id, telegram_username, bound_username))
    conn.commit()

def add_transaction(username, amount, method, status="requested", proof_file_id=None):
    cur.execute("""
    INSERT INTO transactions (username, amount, method, status, proof_file_id, created_at)
    VALUES (?,?,?,?,?,?)
    """, (username, amount, method, status, proof_file_id, datetime.utcnow().isoformat()))
    conn.commit()

# ----------------------------
# Comandos del Bot
# ----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    method, amount = None, None
    if args:
        try:
            parts = args[0].split("_")
            method = parts[0].upper()
            amount = float(parts[1]) if len(parts) > 1 else None
        except:
            method, amount = None, None

    chat_id = update.effective_chat.id
    tg_username = update.effective_user.username or f"user{chat_id}"

    upsert_telegram_link(str(chat_id), tg_username, tg_username)

    msg = [
        f"🎉 Hola @{tg_username}, bienvenido al bot de recargas!",
        "🔹 Envía una foto del comprobante de pago aquí.",
        "🔹 Un admin validará tu recarga y recibirás notificación cuando se acredite tu saldo.",
        "💡 Consejo: asegúrate de que la foto sea clara y legible."
    ]
    if method and amount:
        msg.append(f"✅ Intento registrado: {method} por S/{amount:.2f}")

    await update.message.reply_text("\n".join(msg))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1] if update.message.photo else None
    file_id = photo.file_id if photo else None
    chat_id = str(update.effective_chat.id)

    cur.execute("SELECT bound_username FROM telegram_links WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    username = row["bound_username"] if row else f"user{chat_id}"

    add_transaction(username, 0, "YAPE/USDT/EFECTIVO", status="proof_submitted", proof_file_id=file_id)

    await update.message.reply_text("📸 Comprobante recibido. Un admin lo revisará pronto. ¡Gracias por tu recarga!")

    if STAFF_CHAT_ID and photo:
        try:
            await context.bot.send_photo(
                chat_id=int(STAFF_CHAT_ID),
                photo=file_id,
                caption=f"🚨 Nuevo comprobante recibido\n👤 Usuario: @{username}\n💳 Método: YAPE/USDT/EFECTIVO"
            )
        except Exception as e:
            print(f"Error al enviar foto al staff: {e}")

async def ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.username or ""
    if caller not in ADMINS:
        await update.message.reply_text("❌ No autorizado.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("❗ Uso: /ok @usuario monto")
        return

    username = context.args[0].lstrip("@")
    try:
        amount = float(context.args[1])
    except:
        await update.message.reply_text("❗ Monto inválido")
        return

    # Llamar API para acreditar saldo
    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/agregar_saldo",
            json={"username": username, "monto": amount, "metodo": "ADMIN"},
            headers={"X-SECRET-KEY": API_SECRET},
            timeout=10,
        )
        try:
            data = resp.json()
        except:
            data = resp.text  # muestra el contenido si no es JSON

        if resp.status_code == 200 and isinstance(data, dict) and data.get("ok"):
            await update.message.reply_text(f"✅ Saldo acreditado a {username}: S/{amount:.2f}")
        else:
            await update.message.reply_text(f"❌ Error al acreditar: {data} (status {resp.status_code})")
    except Exception as e:
        await update.message.reply_text(f"❌ Error API: {e}")

# ----------------------------
# Inicialización del bot
# ----------------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN no configurado")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CommandHandler("ok", ok))
    print("🤖 Bot iniciado. Esperando mensajes...")
    app.run_polling()

if __name__ == "__main__":
    main()

