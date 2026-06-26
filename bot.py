import os
import requests
import sqlite3
import secrets
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import threading

# ============ CONFIGURAÇÕES ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_URL = os.getenv("ASAAS_URL", "https://sandbox.asaas.com/api/v3")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagamentos.db")

app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)

# ============ PRODUTOS ============
PRODUTOS = {
    "produto_teste": {"nome": "🧪 Pack de Teste", "preco": 1.00, "descricao": "Pack para testar o pagamento (1 real)"},
    "produto_1": {"nome": "Pack Básico", "preco": 30.00, "descricao": "Pack com 50 fotos"},
    "produto_2": {"nome": "Pack Premium", "preco": 40.00, "descricao": "Pack com 100 fotos + vídeos"},
    "produto_3": {"nome": "Pack VIP", "preco": 60.00, "descricao": "Pack completo + conteúdo exclusivo"},
}

# ============ BANCO DE DADOS ============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS pagamentos
                 (payment_id TEXT PRIMARY KEY, chat_id TEXT, status TEXT, 
                  valor REAL, token TEXT, produto TEXT, tipo TEXT,
                  criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# ============ MENU INICIAL ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛒 Ver Produtos", callback_data="menu_produtos")],
        [InlineKeyboardButton("❤️ Fazer Doação", callback_data="doacao")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 *Bem-vindo à Loja!*\n\n"
        "Escolha uma opção abaixo:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

# ============ MENU DE PRODUTOS ============
async def menu_produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = []
    for key, produto in PRODUTOS.items():
        keyboard.append([InlineKeyboardButton(
            f"{produto['nome']} - R$ {produto['preco']:.2f}", 
            callback_data=f"comprar_{key}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Voltar", callback_data="voltar_inicio")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🛒 *Nossos Produtos:*\n\n"
        "Escolha o que deseja comprar:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

# ============ CONFIRMAR COMPRA ============
async def confirmar_compra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    produto_key = query.data.replace("comprar_", "")
    produto = PRODUTOS.get(produto_key)
    
    if not produto:
        await query.edit_message_text("❌ Produto não encontrado.")
        return
    
    # Salva o produto escolhido no contexto do usuário
    context.user_data["produto_escolhido"] = produto_key
    context.user_data["tipo"] = "produto"
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirmar Compra", callback_data=f"gerar_pix_{produto_key}")],
        [InlineKeyboardButton("🔙 Voltar", callback_data="menu_produtos")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🛒 *{produto['nome']}*\n\n"
        f"💰 Preço: R$ {produto['preco']:.2f}\n"
        f"📝 {produto['descricao']}\n\n"
        f"Deseja confirmar a compra?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

# ============ DOAÇÃO ============
async def doacao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data["tipo"] = "doacao"
    
    await query.edit_message_text(
        "❤️ *Fazer uma Doação*\n\n"
        "Digite o valor que deseja doar (ex: 10, 25, 50):\n\n"
        "Ou escolha um valor:",
        parse_mode="Markdown"
    )
    
    # Aguarda o usuário digitar o valor
    context.user_data["aguardando_valor"] = True

# ============ RECEBER VALOR DA DOAÇÃO ============
async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("aguardando_valor"):
        return
    
    try:
        valor = float(update.message.text.replace(",", "."))
        if valor <= 0:
            await update.message.reply_text("❌ Valor inválido. Digite um número positivo.")
            return
        
        context.user_data["valor_doacao"] = valor
        context.user_data["aguardando_valor"] = False
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirmar Doação", callback_data="gerar_pix_doacao")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="voltar_inicio")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"❤️ *Doação de R$ {valor:.2f}*\n\n"
            f"Deseja confirmar?",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Digite apenas números (ex: 10, 25.50)")

# ============ GERAR PIX ============
async def gerar_pix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.from_user.id
    user = query.from_user
    
    headers = {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json"
    }
    
    # Determina valor e produto
    if context.user_data.get("tipo") == "doacao":
        valor = context.user_data.get("valor_doacao", 10)
        descricao = "Doação"
        produto_key = "doacao"
    else:
        produto_key = query.data.replace("gerar_pix_", "")
        produto = PRODUTOS.get(produto_key)
        if not produto:
            await query.edit_message_text("❌ Erro ao gerar PIX.")
            return
        valor = produto["preco"]
        descricao = produto["nome"]
    
    await query.edit_message_text("⏳ Gerando cobrança PIX...")
    
    try:
        # Cria cliente
        cliente_data = {
            "name": user.full_name,
            "cpfCnpj": "52998224725",
            "email": f"user{chat_id}@goblinfeliz.com"
        }
        
        r_cliente = requests.post(f"{ASAAS_URL}/customers", json=cliente_data, headers=headers, timeout=10)
        cliente = r_cliente.json()
        
        if "id" not in cliente:
            await query.edit_message_text(f"❌ Erro ao criar cliente: {cliente}")
            return
        
        # Cria cobrança
        cobranca_data = {
            "customer": cliente["id"],
            "billingType": "PIX",
            "value": valor,
            "dueDate": "2026-06-27",
            "description": descricao,
            "externalReference": str(chat_id)
        }
        
        r_cobranca = requests.post(f"{ASAAS_URL}/payments", json=cobranca_data, headers=headers, timeout=10)
        cobranca = r_cobranca.json()
        
        if "id" not in cobranca:
            await query.edit_message_text(f"❌ Erro ao criar cobrança: {cobranca}")
            return
        
        # Gera token
        token = secrets.token_urlsafe(16)
        
        # Salva no banco
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO pagamentos (payment_id, chat_id, status, valor, token, produto, tipo) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (cobranca["id"], str(chat_id), "PENDING", valor, token, produto_key, context.user_data.get("tipo", "produto")))
        conn.commit()
        conn.close()
        
        # Pega QR Code
        r_qr = requests.get(f"{ASAAS_URL}/payments/{cobranca['id']}/pixQrCode", headers=headers, timeout=10)
        qr = r_qr.json()
        
        mensagem = (
            f"💰 *{descricao}*\n"
            f"Valor: R$ {valor:.2f}\n\n"
            f"📋 *Copia e Cola:*\n`{qr['payload']}`\n\n"
            f"⏰ Válido até 27/06/2026\n\n"
            f"Após o pagamento, seu material será liberado automaticamente!"
        )
        
        await query.edit_message_text(mensagem, parse_mode="Markdown")
        
    except Exception as e:
        await query.edit_message_text(f"❌ Erro: {str(e)}")

# ============ VOLTAR ============
async def voltar_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🛒 Ver Produtos", callback_data="menu_produtos")],
        [InlineKeyboardButton("❤️ Fazer Doação", callback_data="doacao")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👋 *Bem-vindo à Loja!*\n\n"
        "Escolha uma opção abaixo:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

# ============ WEBHOOK ASAAS ============
@app.route("/webhook/asaas", methods=["POST"])
def webhook_asaas():
    data = request.json
    
    if data.get("event") == "PAYMENT_RECEIVED":
        pagamento = data["payment"]
        payment_id = pagamento["id"]
        chat_id = pagamento.get("externalReference")
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status, token, produto, tipo FROM pagamentos WHERE payment_id = ?", (payment_id,))
        resultado = c.fetchone()
        
        if resultado and resultado[0] == "PENDING":
            token = resultado[1]
            produto_key = resultado[2]
            tipo = resultado[3]
            
            c.execute("UPDATE pagamentos SET status = ? WHERE payment_id = ?", ("RECEIVED", payment_id))
            conn.commit()
            
            if chat_id:
                liberar_conteudo(chat_id, token, produto_key, tipo)
        
        conn.close()
    
    return "OK", 200

def liberar_conteudo(chat_id, token, produto_key, tipo):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    if tipo == "doacao":
        texto = (
            "✅ *Doação recebida!*\n\n"
            "🙏 Muito obrigado pelo seu apoio!\n\n"
            "Seu acesso será liberado em breve."
        )
    else:
        produto = PRODUTOS.get(produto_key, {"nome": "Produto"})
        texto = (
            f"✅ *Pagamento confirmado!*\n\n"
            f"🎉 {produto['nome']} liberado!\n\n"
            f"📥 [Clique aqui para baixar](https://seusite.com/download?token={token})\n\n"
            f"⏳ Link expira em 24 horas."
        )
    
    requests.post(url, json={
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })

# ============ RODAR ============
def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def run_telegram():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(menu_produtos, pattern="^menu_produtos$"))
    application.add_handler(CallbackQueryHandler(confirmar_compra, pattern="^comprar_"))
    application.add_handler(CallbackQueryHandler(gerar_pix, pattern="^gerar_pix_"))
    application.add_handler(CallbackQueryHandler(doacao, pattern="^doacao$"))
    application.add_handler(CallbackQueryHandler(voltar_inicio, pattern="^voltar_inicio$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_valor))
    
    application.run_polling()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    run_telegram()
