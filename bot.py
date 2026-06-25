import os
import requests
import sqlite3
import secrets
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import threading

# ============ CONFIGURAÇÕES ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_URL = os.getenv("ASAAS_URL", "https://sandbox.asaas.com/api/v3")

# Banco de dados SQLite
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagamentos.db")

app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)

# ============ BANCO DE DADOS ============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS pagamentos
                 (payment_id TEXT PRIMARY KEY, chat_id TEXT, status TEXT, 
                  valor REAL, token TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# ============ COMANDOS TELEGRAM ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bem-vindo ao *Jarvis Bot*!\n\n"
        "🛒 Use /comprar para adquirir o E-book Premium por R$ 29,90",
        parse_mode="Markdown"
    )

async def comprar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    await update.message.reply_text("⏳ Gerando cobrança PIX...")
    
    headers = {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json"
    }
    
    cliente_data = {
        "name": user.full_name,
        "cpfCnpj": "52998224725",
        "email": f"user{chat_id}@goblinfeliz.com"
    }
    
    try:
        r_cliente = requests.post(f"{ASAAS_URL}/customers", json=cliente_data, headers=headers, timeout=10)
        cliente = r_cliente.json()
        
        if "id" not in cliente:
            await update.message.reply_text(f"❌ Erro cliente: {cliente}")
            return
        
        cobranca_data = {
            "customer": cliente["id"],
            "billingType": "PIX",
            "value": 29.90,
            "dueDate": "2026-06-26",
            "description": "E-book Premium",
            "externalReference": str(chat_id)
        }
        
        r_cobranca = requests.post(f"{ASAAS_URL}/payments", json=cobranca_data, headers=headers, timeout=10)
        cobranca = r_cobranca.json()
        
        if "id" not in cobranca:
            await update.message.reply_text(f"❌ Erro cobrança: {cobranca}")
            return
        
        token = secrets.token_urlsafe(16)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO pagamentos (payment_id, chat_id, status, valor, token) VALUES (?, ?, ?, ?, ?)",
                  (cobranca["id"], str(chat_id), "PENDING", 29.90, token))
        conn.commit()
        conn.close()
        
        r_qr = requests.get(f"{ASAAS_URL}/payments/{cobranca['id']}/pixQrCode", headers=headers, timeout=10)
        qr = r_qr.json()
        
        mensagem = (
            f"💰 *E-book Premium - R$ 29,90*\n\n"
            f"📋 *Copia e Cola:*\n`{qr['payload']}`\n\n"
            f"Após pagamento, o conteúdo será liberado automaticamente!"
        )
        
        await update.message.reply_text(mensagem, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Erro: {str(e)}")

# ============ WEBHOOK ============
@app.route("/webhook/asaas", methods=["POST"])
def webhook_asaas():
    data = request.json
    
    if data.get("event") == "PAYMENT_RECEIVED":
        pagamento = data["payment"]
        payment_id = pagamento["id"]
        chat_id = pagamento.get("externalReference")
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status, token FROM pagamentos WHERE payment_id = ?", (payment_id,))
        resultado = c.fetchone()
        
        if resultado and resultado[0] == "PENDING":
            token = resultado[1]
            c.execute("UPDATE pagamentos SET status = ? WHERE payment_id = ?", ("RECEIVED", payment_id))
            conn.commit()
            
            if chat_id:
                liberar_conteudo(chat_id, token)
        
        conn.close()
    
    return "OK", 200

def liberar_conteudo(chat_id, token):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    texto = (
        "✅ *Pagamento confirmado!*\n\n"
        f"📥 [Baixar E-book](https://seusite.com/download?token={token})\n\n"
        "⏳ Link expira em 24h."
    )
    requests.post(url, json={
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })

# ============ RODAR AMBOS ============
def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def run_telegram():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("comprar", comprar))
    application.run_polling()

if __name__ == "__main__":
    # Roda Flask em thread separada
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Roda o bot do Telegram (principal)
    run_telegram()