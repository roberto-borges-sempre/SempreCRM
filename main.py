from flask import Flask, request, jsonify
import os
import psycopg2
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURAÇÕES ---
VERIFY_TOKEN = "sempreinternet_segredo_123"
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# --- 1. SETUP BLINDADO (FORÇA ATUALIZAÇÃO) ---
@app.route("/setup_banco", methods=["GET"])
def setup_db():
    log_msgs = []
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. Cria tabelas base se não existirem
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT,
                email TEXT UNIQUE,
                senha TEXT,
                funcao TEXT,
                ativo BOOLEAN DEFAULT TRUE
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contatos (
                id SERIAL PRIMARY KEY,
                whatsapp_id TEXT UNIQUE NOT NULL,
                nome TEXT,
                status_atendimento TEXT DEFAULT 'fila',
                ultima_interacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id SERIAL PRIMARY KEY,
                contato_id INTEGER REFERENCES contatos(id),
                remetente TEXT,
                texto TEXT,
                mensagem_id_meta TEXT,
                data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS respostas_rapidas (
                id SERIAL PRIMARY KEY,
                titulo TEXT,
                texto TEXT,
                criado_por INTEGER REFERENCES usuarios(id)
            );
        """)
        conn.commit()
        log_msgs.append("Tabelas Base verificadas.")

        # 2. LISTA DE COLUNAS NOVAS PARA FORÇAR A CRIAÇÃO
        # Formato: (Tabela, Coluna, Tipo)
        colunas_para_adicionar = [
            ("contatos", "codigo_cliente", "TEXT"),
            ("contatos", "cpf_cnpj", "TEXT"),
            ("contatos", "notas_internas", "TEXT"),
            ("contatos", "vendedora_id", "INTEGER REFERENCES usuarios(id)"),
            ("mensagens", "tipo", "TEXT DEFAULT 'text'"),
            ("mensagens", "url_media", "TEXT"),
            ("mensagens", "custo", "NUMERIC(10, 4) DEFAULT 0.0")
        ]

        # Tenta criar cada coluna individualmente
        for tabela, coluna, tipo in colunas_para_adicionar:
            try:
                cur.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo};")
                conn.commit()
                log_msgs.append(f"✅ Coluna '{coluna}' criada em '{tabela}'.")
            except Exception as e:
                conn.rollback() # Ignora erro se já existir
                # log_msgs.append(f"ℹ️ Coluna '{coluna}' já existia.")

        # 3. Cria Admin se não existir
        cur.execute("""
            INSERT INTO usuarios (nome, email, senha, funcao)
            VALUES ('Administrador', 'admin@sempre.com', '123', 'admin')
            ON CONFLICT (email) DO NOTHING;
        """)
        conn.commit()
        
        cur.close()
        conn.close()
        
        # Retorna o relatório do que foi feito
        return jsonify({"status": "Sucesso", "log": log_msgs}), 200

    except Exception as e:
        return f"Erro Crítico no Banco: {str(e)}", 500

# --- 2. WEBHOOK (IGUAL ANTES) ---
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    if not data: return "Sem dados", 404

    try:
        entry = data['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        
        if 'messages' in value:
            msg_data = value['messages'][0]
            phone = msg_data['from']
            msg_id = msg_data['id']
            contact_name = value['contacts'][0]['profile']['name']
            
            # Tipo de mensagem
            msg_type = msg_data['type']
            texto = ""
            media_id = None
            
            if msg_type == 'text':
                texto = msg_data['text']['body']
            elif msg_type == 'button':
                texto = msg_data['button']['text']
            elif msg_type in ['image', 'audio', 'voice', 'video', 'document']:
                media_id = msg_data[msg_type]['id']
                texto = msg_data[msg_type].get('caption', f"[{msg_type}]")
            else:
                texto = f"[{msg_type}]"

            db_type = 'audio' if msg_type == 'voice' else msg_type

            conn = get_db_connection()
            cur = conn.cursor()

            # Salva Contato (Garante que as colunas novas não quebrem o INSERT)
            # Se der erro aqui, é pq a coluna não existe, mas o setup_banco deve resolver
            cur.execute("""
                INSERT INTO contatos (whatsapp_id, nome, ultima_interacao)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (whatsapp_id) 
                DO UPDATE SET 
                    nome = EXCLUDED.nome, 
                    ultima_interacao = CURRENT_TIMESTAMP,
                    status_atendimento = CASE 
                        WHEN contatos.status_atendimento = 'encerrado' THEN 'fila' 
                        ELSE contatos.status_atendimento 
                    END
                RETURNING id;
            """, (phone, contact_name))
            
            contato_id = cur.fetchone()[0]

            # Salva Mensagem
            cur.execute("""
                INSERT INTO mensagens (contato_id, remetente, texto, tipo, url_media, mensagem_id_meta)
                VALUES (%s, 'cliente', %s, %s, %s, %s)
            """, (contato_id, texto, db_type, media_id, msg_id))

            conn.commit()
            cur.close()
            conn.close()
            print(f"✅ Msg salva: {texto}")

    except Exception as e:
        print(f"⚠️ Erro Webhook: {e}")
        return "Erro processado", 200

    return "Recebido", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
