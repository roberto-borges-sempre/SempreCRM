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

# --- 1. SETUP DO BANCO (V2.0 - COM NOVAS TABELAS) ---
@app.route("/setup_banco", methods=["GET"])
def setup_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # A. TABELA USUARIOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT,
                email TEXT UNIQUE,
                senha TEXT,
                funcao TEXT, -- 'admin' ou 'vendedor'
                ativo BOOLEAN DEFAULT TRUE
            );
        """)

        # B. TABELA CONTATOS (Atualizada com Notas e Códigos)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contatos (
                id SERIAL PRIMARY KEY,
                whatsapp_id TEXT UNIQUE NOT NULL,
                nome TEXT,
                status_atendimento TEXT DEFAULT 'fila', -- 'fila', 'em_andamento', 'encerrado'
                vendedora_id INTEGER REFERENCES usuarios(id),
                codigo_cliente TEXT, -- Novo: (567946)
                cpf_cnpj TEXT,       -- Novo
                notas_internas TEXT, -- Novo: "Cliente bravo", etc
                ultima_interacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Adiciona colunas novas caso a tabela já exista (Migração)
        colunas_novas = [
            ("codigo_cliente", "TEXT"),
            ("cpf_cnpj", "TEXT"),
            ("notas_internas", "TEXT"),
            ("vendedora_id", "INTEGER REFERENCES usuarios(id)")
        ]
        for col, tipo in colunas_novas:
            try:
                cur.execute(f"ALTER TABLE contatos ADD COLUMN {col} {tipo};")
            except:
                conn.rollback()
        
        # C. TABELA MENSAGENS (Suporte a Mídia e Custo)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id SERIAL PRIMARY KEY,
                contato_id INTEGER REFERENCES contatos(id),
                remetente TEXT,
                texto TEXT, -- Se for mídia, aqui vai a legenda ou vazio
                tipo TEXT DEFAULT 'text', -- 'text', 'image', 'audio', 'document', 'template'
                url_media TEXT, -- ID da mídia na Meta ou URL
                custo NUMERIC(10, 4) DEFAULT 0.0, -- Custo do disparo
                mensagem_id_meta TEXT,
                data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Migração Mensagens
        try:
            cur.execute("ALTER TABLE mensagens ADD COLUMN tipo TEXT DEFAULT 'text';")
            cur.execute("ALTER TABLE mensagens ADD COLUMN url_media TEXT;")
            cur.execute("ALTER TABLE mensagens ADD COLUMN custo NUMERIC(10, 4) DEFAULT 0.0;")
        except:
            conn.rollback()

        # D. TABELA RESPOSTAS RÁPIDAS (NOVO!)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS respostas_rapidas (
                id SERIAL PRIMARY KEY,
                titulo TEXT,
                texto TEXT,
                criado_por INTEGER REFERENCES usuarios(id) -- Se NULL, é global
            );
        """)

        # E. ADMIN PADRÃO
        cur.execute("""
            INSERT INTO usuarios (nome, email, senha, funcao)
            VALUES ('Administrador', 'admin@sempre.com', '123', 'admin')
            ON CONFLICT (email) DO NOTHING;
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        return "✅ SUCESSO! Banco V2.0 Atualizado (Notas, Mídia, Respostas Rápidas e Custos).", 200
    except Exception as e:
        return f"Erro ao configurar banco: {str(e)}", 500

# --- 2. WEBHOOK (INTELIGÊNCIA DE RECEBIMENTO) ---
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
            
            # --- IDENTIFICA O TIPO DE MENSAGEM ---
            msg_type = msg_data['type']
            texto = ""
            media_id = None
            
            if msg_type == 'text':
                texto = msg_data['text']['body']
            elif msg_type == 'button':
                texto = msg_data['button']['text']
            elif msg_type in ['image', 'audio', 'voice', 'video', 'document', 'sticker']:
                # Pega o ID da mídia para baixar depois no Frontend
                media_id = msg_data[msg_type]['id']
                # Se tiver legenda (caption), salva no texto
                texto = msg_data[msg_type].get('caption', f"[{msg_type}]")
            else:
                texto = f"[{msg_type} - Não suportado]"

            # Normaliza voice para audio
            db_type = 'audio' if msg_type == 'voice' else msg_type

            conn = get_db_connection()
            cur = conn.cursor()

            # 1. Atualiza Contato (Reabre se estiver encerrado?)
            # Por enquanto, se o cliente manda msg, volta para 'fila' se estava 'encerrado'
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

            # 2. Salva Mensagem
            cur.execute("""
                INSERT INTO mensagens (contato_id, remetente, texto, tipo, url_media, mensagem_id_meta)
                VALUES (%s, 'cliente', %s, %s, %s, %s)
            """, (contato_id, texto, db_type, media_id, msg_id))

            conn.commit()
            cur.close()
            conn.close()
            print(f"✅ Msg de {contact_name} ({db_type}) salva.")

    except Exception as e:
        print(f"⚠️ Erro Webhook: {e}")
        return "Erro processado", 200

    return "Recebido", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
