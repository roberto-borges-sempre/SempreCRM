from fastapi import FastAPI, Request, HTTPException
from sqlalchemy import create_engine, text
import os
import json

app = FastAPI()

# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
# O sistema vai buscar o link do Neon nas configurações do servidor (Render)
DATABASE_URL = os.environ.get("DATABASE_URL")

# Ajuste técnico para o link funcionar
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Se não tiver link configurado (ex: rodando local sem configurar), avisa
if not DATABASE_URL:
    print("⚠️ AVISO: DATABASE_URL não encontrada via variáveis de ambiente.")
    engine = None
else:
    engine = create_engine(DATABASE_URL)

# --- 1. VERIFICAÇÃO (O aperto de mão com a Meta) ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    A Meta acessa esse link para confirmar que o servidor é seu.
    A senha aqui é 'sempre_token', você vai colocar isso na Meta depois.
    """
    VERIFY_TOKEN = "sempre_token" 
    
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return int(challenge)
        else:
            raise HTTPException(status_code=403, detail="Token errado")
    return {"status": "ok"}

# --- 2. RECEBIMENTO (Guardar no Neon) ---
@app.post("/webhook")
async def receive_message(request: Request):
    if not engine:
        return {"status": "error", "detail": "Sem conexão com Banco"}

    data = await request.json()
    
    try:
        # Navega no JSON da Meta para achar a mensagem
        entry = data['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        
        if 'messages' in value:
            msg_data = value['messages'][0]
            phone_number = msg_data['from'] # Ex: 55319999...
            msg_text = ""
            msg_id_meta = msg_data['id']
            
            # Descobre o nome do cliente (a Meta manda junto)
            contact_name = value['contacts'][0]['profile']['name']

            # Extrai o texto dependendo do tipo
            if msg_data['type'] == 'text':
                msg_text = msg_data['text']['body']
            elif msg_data['type'] == 'button':
                msg_text = msg_data['button']['text']
            elif msg_data['type'] == 'interactive':
                 if 'button_reply' in msg_data['interactive']:
                    msg_text = msg_data['interactive']['button_reply']['title']
            
            # --- CONEXÃO COM O BANCO ---
            with engine.connect() as conn:
                # A. Garante que o contato existe na tabela 'contatos'
                # Se não existir, cria e coloca na fila. Se existir, atualiza a hora.
                conn.execute(text("""
                    INSERT INTO contatos (whatsapp_id, nome, status_atendimento, ultima_interacao)
                    VALUES (:phone, :name, 'fila', NOW())
                    ON CONFLICT (whatsapp_id) DO UPDATE 
                    SET nome = :name, ultima_interacao = NOW()
                """), {"phone": phone_number, "name": contact_name})
                
                # B. Pega o ID desse contato
                result = conn.execute(text("SELECT id FROM contatos WHERE whatsapp_id = :phone"), {"phone": phone_number})
                contato_id = result.scalar()
                
                # C. Salva a mensagem na tabela 'mensagens'
                conn.execute(text("""
                    INSERT INTO mensagens (contato_id, remetente, texto, mensagem_id_meta)
                    VALUES (:cid, 'cliente', :txt, :mid)
                """), {"cid": contato_id, "txt": msg_text, "mid": msg_id_meta})
                
                conn.commit()
                print(f"✅ Mensagem salva de {contact_name}: {msg_text}")

    except Exception as e:
        print(f"Erro processando msg: {e}")
        # Retornamos 200 OK para a Meta não ficar reenviando infinitamente em caso de erro nosso
        return {"status": "error", "detail": str(e)}

    return {"status": "received"}
