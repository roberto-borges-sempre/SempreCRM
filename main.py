from flask import Flask, request, jsonify
import os
import psycopg2
from datetime import datetime

app = Flask(__name__)

# Configurações
VERIFY_TOKEN = "sempreinternet_segredo_123"
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# Rota para Criar a Tabela (Rode uma vez)
@app.route("/setup_banco", methods=["GET"])
def setup_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Cria a tabela de mensagens se não existir
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id SERIAL PRIMARY KEY,
                nome TEXT,
                telefone TEXT,
                texto TEXT,
                data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        return "Tabela 'mensagens' criada (ou já existia) com sucesso!", 200
    except Exception as e:
        return f"Erro ao criar tabela: {str(e)}", 500

# Rota para VER as mensagens no navegador
@app.route("/ver_mensagens", methods=["GET"])
def ver_mensagens():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT nome, telefone, texto, data_envio FROM mensagens ORDER BY data_envio DESC LIMIT 50;")
        mensagens = cur.fetchall()
        cur.close()
        conn.close()
        
        # Monta um HTML simples
        html = "<h1>Últimas Mensagens Recebidas</h1><ul>"
        for msg in mensagens:
            html += f"<li><b>{msg[0]}</b> ({msg[1]}): {msg[2]} <br><small>{msg[3]}</small></li><hr>"
        html += "</ul>"
        
        return html, 200
    except Exception as e:
        return f"Erro ao ler banco: {str(e)}", 500

# Webhook (Verificação da Meta)
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
    return "Falha na verificação", 403

# Webhook (Recebimento de Mensagens)
@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    if data:
        try:
            entry = data['entry'][0]
            changes = entry['changes'][0]
            value = changes['value']
            
            if 'messages' in value:
                message = value['messages'][0]
                # Dados para salvar
                numero = message['from']
                texto = message['text']['body']
                nome = value['contacts'][0]['profile']['name']
                
                # GRAVANDO NO BANCO NEON
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO mensagens (nome, telefone, texto) VALUES (%s, %s, %s)",
                    (nome, numero, texto)
                )
                conn.commit()
                cur.close()
                conn.close()
                
                print(f"Mensagem de {nome} salva no banco!")

        except Exception as e:
            print(f"Erro ao processar: {e}")

        return "Recebido", 200
    return "Sem dados", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
