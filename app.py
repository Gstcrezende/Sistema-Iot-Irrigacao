import os
import ssl
import json
import time
import requests
import threading
from flask import Flask, jsonify, request, render_template
import paho.mqtt.client as mqtt

app = Flask(__name__)

# =====================
# CONFIGURAÇÕES (Use Variáveis de Ambiente no Render se possível)
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"

TOPIC_DADOS = "/fazenda/dados"
TOPIC_CONTROLE = "/fazenda/irrigacao"

API_KEY = "1563f0caa3ed84c078ab47087d40a962"
CIDADE = "Ribeirao Preto,BR"

# =====================
# ESTADO GLOBAL
# =====================
dados = {
    "solo": 0,
    "temp": 0,
    "temp_cidade": 0,
    "chuva": False,
    "cultura": "milho",
    "irrigacao": "OFF"
}

regras = {
    "milho": {"min": 40, "max": 70},
    "soja": {"min": 35, "max": 65},
    "cafe": {"min": 45, "max": 75}
}

# =====================
# LÓGICA DE CLIMA (Background)
# =====================
def buscar_clima_api():
    """Faz a requisição para a OpenWeather de forma segura."""
    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": CIDADE,
            "appid": API_KEY,
            "units": "metric",
            "lang": "pt_br"
        }
        # O 'params' cuida dos espaços em branco no nome da cidade automaticamente
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if response.status_code == 200:
            temp = data["main"]["temp"]
            # Verifica se há chuva nos dados meteorológicos
            clima_main = data["weather"][0]["main"].lower()
            chuva = any(x in clima_main for x in ["rain", "drizzle", "thunderstorm"])
            return True, chuva, temp
        else:
            print(f"Erro API OpenWeather: {data.get('message')}")
            return False, False, 0
    except Exception as e:
        print(f"Erro ao conectar na API: {e}")
        return False, False, 0

def tarefa_atualizar_clima():
    """Thread que atualiza o clima a cada 10 minutos."""
    global dados
    while True:
        sucesso, chuva, temp_cidade = buscar_clima_api()
        if sucesso:
            dados["chuva"] = chuva
            dados["temp_cidade"] = temp_cidade
            print(f"Clima atualizado: {temp_cidade}°C, Chuva: {chuva}")
        
        # Espera 10 minutos (600s) para não estourar o limite da conta gratuita
        time.sleep(600)

# =====================
# LÓGICA DE IRRIGAÇÃO
# =====================
def decidir_irrigacao(solo, temp_sensor, cultura, esta_chovendo):
    regra = regras[cultura]

    if esta_chovendo:
        return "OFF"

    if solo < regra["min"]:
        return "ON"

    if regra["min"] <= solo <= regra["max"]:
        # Se estiver muito quente no sensor local, reforça a irrigação
        if temp_sensor > 30:
            return "ON"
        return "OFF"

    return "OFF"

# =====================
# MQTT CALLBACKS
# =====================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Conectado ao HiveMQ com sucesso!")
        client.subscribe(TOPIC_DADOS)
    else:
        print(f"Falha na conexão MQTT. Código: {rc}")

def on_message(client, userdata, msg):
    global dados
    try:
        payload = json.loads(msg.payload.decode())
        
        # Atualiza dados do sensor (ESP32)
        dados["solo"] = payload.get("solo", 0)
        dados["temp"] = payload.get("temp", 0)

        # Decide a irrigação com base no estado ATUAL (que a thread de clima mantém)
        estado = decidir_irrigacao(
            dados["solo"],
            dados["temp"],
            dados["cultura"],
            dados["chuva"]
        )

        dados["irrigacao"] = estado
        client.publish(TOPIC_CONTROLE, estado)
        
    except Exception as e:
        print(f"Erro ao processar mensagem MQTT: {e}")

# =====================
# INICIALIZAÇÃO MQTT
# =====================
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)

client.on_connect = on_connect
client.on_message = on_message

# =====================
# ROTAS FLASK
# =====================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dados")
def get_dados():
    return jsonify(dados)

@app.route("/cultura", methods=["POST"])
def set_cultura():
    nova_cultura = request.json.get("cultura")
    if nova_cultura in regras:
        dados["cultura"] = nova_cultura
        return {"ok": True}
    return {"ok": False}, 400

@app.route("/ligar")
def ligar():
    client.publish(TOPIC_CONTROLE, "ON")
    dados["irrigacao"] = "ON"
    return {"ok": True}

@app.route("/desligar")
def desligar():
    client.publish(TOPIC_CONTROLE, "OFF")
    dados["irrigacao"] = "OFF"
    return {"ok": True}

# =====================
# EXECUÇÃO
# =====================
if __name__ == "__main__":
    # 1. Inicia a thread de clima antes de tudo
    t = threading.Thread(target=tarefa_atualizar_clima, daemon=True)
    t.start()

    # 2. Conecta ao MQTT
    try:
        client.connect(MQTT_HOST, MQTT_PORT)
        client.loop_start()
    except Exception as e:
        print(f"Não foi possível conectar ao Broker: {e}")

    # 3. Roda o Flask (Configurado para o Render)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
