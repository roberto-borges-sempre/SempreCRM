from flask import Flask, request, jsonify
import os

app = Flask(__name__)

# SEU TOKEN DE VERIFICAÇÃO (Você vai por isso lá na Meta)
VERIFY_TOKEN = "sempreinternet_segredo_123"

@app.route("/", methods=["GET"])
def home():
    return "SempreCRM API está rodando! (Python/Flask)", 200

# Rota para a Meta verificar se você existe (GET)
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return challenge, 200
        else:
            return "Token inválido", 403
    return "Olá! Configure o Webhook na Meta.", 200

# Rota para RECEBER as mensagens (POST)
@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    
    if data:
        # Verifica se tem mensagens
        try:
            entry = data['entry'][0]
            changes = entry['changes'][0]
            value = changes['value']
            
            if 'messages' in value:
                message = value['messages'][0]
                numero_cliente = message['from']
                texto_cliente = message['text']['body']
                nome_cliente = value['contacts'][0]['profile']['name']
                
                print(f"\nNOVA MENSAGEM DE: {nome_cliente} ({numero_cliente})")
                print(f"DIZENDO: {texto_cliente}")
                print("-" * 30)
                
        except KeyError:
            pass # Ignora status de entrega (lido/entregue) por enquanto

        return "Recebido", 200
    else:
        return "Sem dados", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
