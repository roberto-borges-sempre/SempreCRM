from flask import Flask, request, jsonify
import os
import psycopg2
import requests # Importante para o robô enviar msg
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURAÇÕES ---
VERIFY_TOKEN = "sempreinternet_segredo_123"
DATABASE_URL = os.environ.get("DATABASE_URL")
# Precisamos do Token aqui para o Robô responder sozinho
META_TOKEN = os.environ.get("META_TOKEN") # Opcional: Pegar das envs se tiver, ou hardcode abaixo
# Se não configurou META_TOKEN nas variáveis de ambiente do Render, 
# o robô não conseguirá enviar. Mas o app.py continuará funcionando.

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# --- FUNÇÃO AUXILIAR: ENVIAR MENSAGEM (ROBÔ) ---
def enviar_mensagem_robo(telefone, texto):
    # Tenta pegar credenciais do banco ou variaveis (Simplificado: Hardcode ou Env)
    # Para facilitar, vamos assumir que você vai configurar META_TOKEN e META_PHONE_ID 
    # nas "Environment Variables" do Render, igual fez no Streamlit.
    token = os.environ.get("META_TOKEN") 
    phone_id = os.environ.get("META_PHONE_ID", "908486432354190")
    
    if not token: 
        print("⚠️ Robô sem Token configurado no Render. Mensagem não enviada.")
        return

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # Tratamento telefone
    tel = ''.join(filter(str.isdigit, str(telefone)))
    if len(tel) == 13 and tel.startswith("55"): tel = tel[:4] + tel[5:]
    
    data = {
        "messaging_product": "whatsapp",
        "to": tel,
        "type": "text",
        "text": {"body": texto}
    }
    try:
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f"Erro envio robô: {e}")

# --- 1. SETUP DO BANCO (V3.0) ---
@app.route("/setup_banco", methods=["GET"])
def setup_db():
    log = []
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Tabelas Base
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
        # NOVA TABELA: CONFIGURAÇÕES (Para guardar a Saudação)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS configuracoes (
                chave TEXT PRIMARY KEY,
                valor TEXT
            );
        """)
        # Insere saudação padrão se não existir
        cur.execute("""
            INSERT INTO configuracoes (chave, valor) 
            VALUES ('msg_boas_vindas', 'Olá! Bem-vindo à Sempre Internet. Um atendente falará com você em instantes.')
            ON CONFLICT (chave) DO NOTHING;
        """)
        log.append("Tabela Configurações criada.")

        # Força colunas novas (caso V2 não tenha rodado)
        cols = [
            ("contatos", "codigo_cliente", "TEXT"),
            ("contatos", "cpf_cnpj", "TEXT"),
            ("contatos", "notas_internas", "TEXT"),
            ("contatos", "vendedora_id", "INTEGER REFERENCES usuarios(id)"),
            ("mensagens", "tipo", "TEXT DEFAULT 'text'"),
            ("mensagens", "url_media", "TEXT"),
            ("mensagens", "custo", "NUMERIC(10, 4) DEFAULT 0.0")
        ]
        for tab, col, tipo in cols:
            try:
                cur.execute(f"ALTER TABLE {tab} ADD COLUMN {col} {tipo};")
                conn.commit()
                log.append(f"Coluna {col} ok.")
            except:
                conn.rollback()

        # Admin Padrão
        cur.execute("INSERT INTO usuarios (nome, email, senha, funcao) VALUES ('Admin', 'admin@sempre.com', '123', 'admin') ON CONFLICT (email) DO NOTHING;")
        conn.commit()
        
        cur.close()
        conn.close()
        return jsonify({"status": "Sucesso V3", "log": log}), 200
    except Exception as e:
        return f"Erro: {str(e)}", 500

# --- 2. WEBHOOK INTELIGENTE ---
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Erro Token", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    if not data: return "No data", 404

    try:
        entry = data['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        
        if 'messages' in value:
            msg_data = value['messages'][0]
            phone = msg_data['from']
            msg_id = msg_data['id']
            contact_name = value['contacts'][0]['profile']['name']
            
            # Tipo msg
            tipo = msg_data['type']
            texto = ""
            media_id = None
            if tipo == 'text': texto = msg_data['text']['body']
            elif tipo in ['image','audio','voice','document']: 
                media_id = msg_data[tipo]['id']
                texto = msg_data[tipo].get('caption', f"[{tipo}]")
            else: texto = f"[{tipo}]"
            
            db_type = 'audio' if tipo == 'voice' else tipo

            conn = get_db_connection()
            cur = conn.cursor()

            # LÓGICA DO ROBÔ: Verifica status ATUAL antes de atualizar
            cur.execute("SELECT status_atendimento FROM contatos WHERE whatsapp_id = %s", (phone,))
            resultado = cur.fetchone()
            
            deve_saudar = False
            status_atual = None
            
            if not resultado:
                # Cliente Novo (Nunca falou antes)
                deve_saudar = True
                status_atual = 'fila'
            else:
                status_atual = resultado[0]
                if status_atual == 'encerrado':
                    # Cliente voltando
                    deve_saudar = True
            
            # Atualiza/Cria Contato (Trazendo para fila)
            cur.execute("""
                INSERT INTO contatos (whatsapp_id, nome, ultima_interacao, status_atendimento)
                VALUES (%s, %s, CURRENT_TIMESTAMP, 'fila')
                ON CONFLICT (whatsapp_id) 
                DO UPDATE SET 
                    nome = EXCLUDED.nome, 
                    ultima_interacao = CURRENT_TIMESTAMP,
                    status_atendimento = 'fila' -- Sempre reabre se mandar msg
                RETURNING id;
            """, (phone, contact_name))
            contato_id = cur.fetchone()[0]

            # Salva Mensagem do Cliente
            cur.execute("""
                INSERT INTO mensagens (contato_id, remetente, texto, tipo, url_media, mensagem_id_meta)
                VALUES (%s, 'cliente', %s, %s, %s, %s)
            """, (contato_id, texto, db_type, media_id, msg_id))

            # DISPARA SAUDAÇÃO SE NECESSÁRIO
            if deve_saudar:
                # Busca mensagem configurada
                cur.execute("SELECT valor FROM configuracoes WHERE chave='msg_boas_vindas'")
                res_config = cur.fetchone()
                msg_saudacao = res_config[0] if res_config else "Olá!"
                
                if msg_saudacao and msg_saudacao.strip() != "":
                    # Envia no Zap (via requests)
                    enviar_mensagem_robo(phone, msg_saudacao)
                    
                    # Salva no histórico como 'empresa' (automático)
                    cur.execute("""
                        INSERT INTO mensagens (contato_id, remetente, texto, tipo)
                        VALUES (%s, 'empresa', %s, 'text')
                    """, (contato_id, msg_saudacao))
                    print(f"🤖 Robô saudou {contact_name}")

            conn.commit()
            cur.close()
            conn.close()

    except Exception as e:
        print(f"Erro Webhook: {e}")
        return "Erro", 200

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
