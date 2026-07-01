import os
import requests
import sqlite3
import secrets
import re
import io
import base64
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import threading

# ============ CONFIGURACOES ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_URL = os.getenv("ASAAS_URL", "https://sandbox.asaas.com/api/v3")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagamentos.db")

app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)

# ============ PRODUTOS ============
PRODUTOS = {
    "pack_basico": {
        "nome": "Pack Basico",
        "preco": 30.00,
        "descricao": "Pack com conteudo basico e exclusivo"
    },
    "pack_premium": {
        "nome": "Pack Premium",
        "preco": 80.00,
        "descricao": "Pack completo com conteudo premium e bonus"
    },
    "pack_vip": {
        "nome": "Pack VIP",
        "preco": 110.00,
        "descricao": "Pack VIP com todo conteudo + acesso exclusivo ao grupo"
    },
    
}

# ============ FUNCAO PARA LIMPAR CARACTERES ============
def limpar_texto(texto, max_length=100):
    if not texto:
        return "Sem descricao"
    texto_limpo = re.sub(r'[^\w\s]', '', texto)
    texto_limpo = re.sub(r'\s+', ' ', texto_limpo).strip()
    return texto_limpo[:max_length] if texto_limpo else "Sem descricao"

# ============ BANCO DE DADOS ============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS pagamentos
                 (payment_id TEXT PRIMARY KEY, chat_id TEXT, status TEXT, 
                  valor REAL, token TEXT, produto TEXT, tipo TEXT,
                  criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("PRAGMA table_info(pagamentos)")
    colunas = [col[1] for col in c.fetchall()]

    if 'produto' not in colunas:
        c.execute("ALTER TABLE pagamentos ADD COLUMN produto TEXT")
        print("[DB] Coluna 'produto' adicionada")

    if 'tipo' not in colunas:
        c.execute("ALTER TABLE pagamentos ADD COLUMN tipo TEXT")
        print("[DB] Coluna 'tipo' adicionada")

    conn.commit()
    conn.close()
    print("[DB] Banco de dados inicializado com sucesso!")

# ============ MENU INICIAL ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Ver Produtos", callback_data="menu_produtos")],
        [InlineKeyboardButton("Fazer Doacao", callback_data="doacao")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Bem-vindo a Loja!\n\nEscolha uma opcao abaixo:",
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

    keyboard.append([InlineKeyboardButton("Voltar", callback_data="voltar_inicio")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "Nossos Produtos:\n\nEscolha o que deseja comprar:",
        reply_markup=reply_markup
    )

# ============ CONFIRMAR COMPRA ============
async def confirmar_compra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    produto_key = query.data.replace("comprar_", "")
    produto = PRODUTOS.get(produto_key)

    if not produto:
        await query.edit_message_text("Produto nao encontrado.")
        return

    context.user_data["produto_escolhido"] = produto_key
    context.user_data["tipo"] = "produto"

    keyboard = [
        [InlineKeyboardButton("Confirmar Compra", callback_data=f"gerar_pix_{produto_key}")],
        [InlineKeyboardButton("Voltar", callback_data="menu_produtos")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"{produto['nome']}\n\nPreco: R$ {produto['preco']:.2f}\n{produto['descricao']}\n\nDeseja confirmar a compra?",
        reply_markup=reply_markup
    )

# ============ DOACAO ============
async def doacao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["tipo"] = "doacao"
    context.user_data["aguardando_valor"] = True

    await query.edit_message_text(
        "Fazer uma Doacao\n\nDigite o valor que deseja doar (ex: 10, 25, 50):"
    )

# ============ RECEBER VALOR DA DOACAO ============
async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("aguardando_valor"):
        return

    try:
        valor = float(update.message.text.replace(",", "."))
        if valor <= 0:
            await update.message.reply_text("Valor invalido. Digite um numero positivo.")
            return

        context.user_data["valor_doacao"] = valor
        context.user_data["aguardando_valor"] = False

        keyboard = [
            [InlineKeyboardButton("Confirmar Doacao", callback_data="gerar_pix_doacao")],
            [InlineKeyboardButton("Cancelar", callback_data="voltar_inicio")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"Doacao de R$ {valor:.2f}\n\nDeseja confirmar?",
            reply_markup=reply_markup
        )

    except ValueError:
        await update.message.reply_text("Valor invalido. Digite apenas numeros (ex: 10, 25.50)")

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
        descricao = "Doacao"
        produto_key = "doacao"
    else:
        produto_key = query.data.replace("gerar_pix_", "")
        produto = PRODUTOS.get(produto_key)
        if not produto:
            await query.edit_message_text("Erro ao gerar PIX.")
            return
        valor = produto["preco"]
        descricao = produto["nome"]

    await query.edit_message_text("Gerando cobranca PIX...")

    try:
        # LIMPA O NOME DO USUARIO
        nome_limpo = limpar_texto(user.full_name, 50)
        if not nome_limpo:
            nome_limpo = f"Cliente {chat_id}"

        # Cria cliente
        cliente_data = {
            "name": nome_limpo,
            "cpfCnpj": "52998224725",
            "email": f"user{chat_id}@goblinfeliz.com"
        }

        r_cliente = requests.post(f"{ASAAS_URL}/customers", json=cliente_data, headers=headers, timeout=10)
        cliente = r_cliente.json()

        if "id" not in cliente:
            await query.edit_message_text(f"Erro ao criar cliente: {cliente}")
            return

        # LIMPA A DESCRICAO
        descricao_limpa = limpar_texto(descricao, 100)

        # Cria cobranca
        cobranca_data = {
            "customer": cliente["id"],
            "billingType": "PIX",
            "value": valor,
            "dueDate": "2026-06-30",
            "description": descricao_limpa,
            "externalReference": str(chat_id)
        }

        r_cobranca = requests.post(f"{ASAAS_URL}/payments", json=cobranca_data, headers=headers, timeout=10)
        cobranca = r_cobranca.json()

        if "id" not in cobranca:
            await query.edit_message_text(f"Erro ao criar cobranca: {cobranca}")
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

        # ===== NOVO: Envia imagem do QR Code + mensagem com Copia e Cola =====
        payload = qr.get('payload', 'Codigo nao disponivel')
        imagem_qr_base64 = qr.get('encodedImage')  # Asaas retorna imagem em base64

        mensagem = (
            f"<b>{descricao}</b>\n"
            f"Valor: <b>R$ {valor:.2f}</b>\n\n"
            f"<b>Copia e Cola:</b>\n"
            f"<code>{payload}</code>\n\n"
            f"Valido ate 01/07/2026\n\n"
            f"Apos o pagamento, seu material sera liberado automaticamente!"
        )

        # Se tiver imagem base64 do QR Code, envia como foto
        if imagem_qr_base64:
            try:
                imagem_bytes = base64.b64decode(imagem_qr_base64)
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=imagem_bytes,
                    caption=mensagem,
                    parse_mode="HTML"
                )
                # Apaga a mensagem "Gerando cobranca PIX..."
                await query.delete_message()
            except Exception as e:
                print(f"[ERRO] Ao enviar imagem do QR: {e}")
                # Fallback: envia so texto
                await query.edit_message_text(mensagem, parse_mode="HTML")
        else:
            # Sem imagem, envia so texto
            await query.edit_message_text(mensagem, parse_mode="HTML")

    except Exception as e:
        await query.edit_message_text(f"Erro: {str(e)}")

# ============ VOLTAR ============
async def voltar_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("Ver Produtos", callback_data="menu_produtos")],
        [InlineKeyboardButton("Fazer Doacao", callback_data="doacao")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "Bem-vindo a Loja!\n\nEscolha uma opcao abaixo:",
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
            "Pagamento confirmado!\n\n"
            "Muito obrigado pelo seu apoio!\n\n"
            "Seu acesso sera liberado em breve."
        )
    else:
        produto = PRODUTOS.get(produto_key, {"nome": "Produto"})
        texto = (
            f"Pagamento confirmado!\n\n"
            f"{produto['nome']} liberado!\n\n"
            f"[Clique aqui para baixar](https://seusite.com/download?token={token})\n\n"
            f"Link expira em 24 horas."
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(menu_produtos, pattern="^menu_produtos$"))
    application.add_handler(CallbackQueryHandler(confirmar_compra, pattern="^comprar_"))
    application.add_handler(CallbackQueryHandler(gerar_pix, pattern="^gerar_pix_"))
    application.add_handler(CallbackQueryHandler(doacao, pattern="^doacao$"))
    application.add_handler(CallbackQueryHandler(voltar_inicio, pattern="^voltar_inicio$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_valor))

    application.run_polling()

if __name__ == "__main__":
    init_db()

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    run_telegram()
