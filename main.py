from flask import Flask, request, jsonify
import os
import psycopg2
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURAÇÕES ---
# Token de verificação da Meta (Você define)
VERIFY_TOKEN = "sempreinternet_segredo_123"
# A URL do Banco vem automaticamente das variáveis do Render
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    """Conecta no banco Neon/Postgres"""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# --- 1. CONFIGURAÇÃO DO BANCO (RODE ISSO PARA CRIAR AS TABELAS) ---
@app.route("/setup_banco", methods=["GET"])
def setup_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # A. Tabela USUARIOS (Para login no painel)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT,
                email TEXT UNIQUE,
                senha TEXT, -- Em produção idealmente usaria hash
                funcao TEXT, -- 'admin' ou 'vendedor'
                ativo BOOLEAN DEFAULT TRUE
            );
        """)

        # B. Tabela CONTATOS (Clientes)
        # Adicionamos a coluna vendedora_id para saber quem atende
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contatos (
                id SERIAL PRIMARY KEY,
                whatsapp_id TEXT UNIQUE NOT NULL,
                nome TEXT,
                status_atendimento TEXT DEFAULT 'fila',
                ultima_interacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Tenta adicionar a coluna vendedora_id caso a tabela já existisse sem ela
        try:
            cur.execute("ALTER TABLE contatos ADD COLUMN vendedora_id INTEGER REFERENCES usuarios(id);")
        except:
            conn.rollback() # Ignora se a coluna já existir
        
        # C. Tabela MENSAGENS (Histórico)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id SERIAL PRIMARY KEY,
                contato_id INTEGER REFERENCES contatos(id),
                remetente TEXT, -- 'cliente', 'empresa'
                texto TEXT,
                mensagem_id_meta TEXT,
                data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # D. CRIAR USUÁRIO ADMIN PADRÃO (Se não existir)
        # Login: admin@sempre.com | Senha: 123
        cur.execute("""
            INSERT INTO usuarios (nome, email, senha, funcao)
            VALUES ('Administrador', 'admin@sempre.com', '123', 'admin')
            ON CONFLICT (email) DO NOTHING;
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        return "✅ SUCESSO! Tabelas Criadas e Usuário Admin (admin@sempre.com / 123) configurado.", 200
    except Exception as e:
        return f"Erro ao configurar banco: {str(e)}", 500

# --- 2. VISUALIZADOR RÁPIDO (DEBUG) ---
@app.route("/ver_tudo", methods=["GET"])
def ver_tudo():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Mostra as últimas 20 mensagens com o nome do cliente
        cur.execute("""
            SELECT c.nome, c.whatsapp_id, m.texto, m.remetente, m.data_envio 
            FROM mensagens m
            JOIN contatos c ON m.contato_id = c.id
            ORDER BY m.data_envio DESC LIMIT 20;
        """)
        rows = cur.fetchall()
        
        html = "<h1>Histórico CRM (Últimas 20)</h1><ul>"
        for row in rows:
            icone = "👤" if row[3] == 'cliente' else "🏢"
            html += f"<li>{icone} <b>{row[0]}</b>: {row[2]} <br><small>{row[4]}</small></li><hr>"
        html += "</ul>"
        
        cur.close()
        conn.close()
        return html
    except Exception as e:
        return f"Erro ao ler banco: {e}"

# --- 3. VERIFICAÇÃO DA META (WEBHOOK) ---
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
    return "Token inválido", 403

# --- 4. RECEBIMENTO DE MENSAGENS (O CÉREBRO) ---
@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    
    if not data:
        return "Sem dados", 404

    try:
        # Navega no JSON complexo da Meta
        entry = data['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        
        if 'messages' in value:
            msg_data = value['messages'][0]
            phone_number = msg_data['from']
            msg_id_meta = msg_data['id']
            contact_name = value['contacts'][0]['profile']['name']
            
            # Verifica se é texto
            msg_text = ""
            if msg_data['type'] == 'text':
                msg_text = msg_data['text']['body']
            else:
                msg_text = f"[{msg_data['type']}]" # Foto, áudio, etc

            # CONEXÃO COM O BANCO PARA SALVAR
            conn = get_db_connection()
            cur = conn.cursor()

            # 1. Cria ou Atualiza o Contato
            cur.execute("""
                INSERT INTO contatos (whatsapp_id, nome, ultima_interacao)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (whatsapp_id) 
                DO UPDATE SET nome = EXCLUDED.nome, ultima_interacao = CURRENT_TIMESTAMP
                RETURNING id;
            """, (phone_number, contact_name))
            
            contato_id = cur.fetchone()[0]

            # 2. Salva a Mensagem
            cur.execute("""
                INSERT INTO mensagens (contato_id, remetente, texto, mensagem_id_meta)
                VALUES (%s, 'cliente', %s, %s)
            """, (contato_id, msg_text, msg_id_meta))

            conn.commit()
            cur.close()
            conn.close()

            print(f"✅ CRM: Mensagem de {contact_name} salva no ID {contato_id}")

    except Exception as e:
        print(f"⚠️ Erro ao processar mensagem: {e}")
        # Retorna 200 para a Meta não ficar tentando reenviar em loop se der erro no nosso código
        return "Erro processado", 200

    return "Recebido", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
