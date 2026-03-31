import os
import ssl
import json
import requests
import time  # 🔥 Novo import adicionado para controlar o tempo
from flask import Flask, jsonify, request, render_template
import paho.mqtt.client as mqtt

app = Flask(__name__)

# =====================
# CONFIG
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"

TOPIC_DADOS = "/fazenda/dados"
TOPIC_CONTROLE = "/fazenda/irrigacao"

API_KEY = "1563f0caa3ed84c078ab47087d40a962"

# 🔥 TROQUEI PRA FRANCA
CIDADE = "Franca,BR"

# =====================
# ESTADO
# =====================
dados = {
    "solo": 0,
    "temp": 0,
    "temp_cidade": 0,
    "chuva": False,
    "cultura": "milho",
    "irrigacao": "OFF",
    "cidade": "-",
    "icone": "",
    "lat": 0,
    "lon": 0
}

regras = {
    "milho": {"min": 40, "max": 70},
    "soja": {"min": 35, "max": 65},
    "cafe": {"min": 45, "max": 75}
}

ultima_busca_clima = 0  # 🔥 Variável para guardar o horário da última requisição do clima

# =====================
# CLIMA (SEM THREAD)
# =====================
def buscar_clima():
    try:
        url = "https://api.openweathermap.org/data/2.5/weather"

        params = {
            "q": CIDADE,
            "appid": API_KEY,
            "units": "metric",
            "lang": "pt_br"
        }

        print("\n🔎 Buscando clima...")
        print("Cidade:", CIDADE)

        r = requests.get(url, params=params, timeout=5)

        print("Status:", r.status_code)
        print("Resposta:", r.text)

        data = r.json()

        if r.status_code != 200:
            print("❌ ERRO API:", data)
            return

        dados["temp_cidade"] = data["main"]["temp"]
        dados["cidade"] = data["name"]
        dados["icone"] = data["weather"][0]["icon"]
        dados["lat"] = data["coord"]["lat"]
        dados["lon"] = data["coord"]["lon"]

        clima = data["weather"][0]["main"].lower()
        dados["chuva"] = clima in ["rain", "drizzle", "thunderstorm"]

        print("✅ Clima atualizado!")

    except Exception as e:
        print("🔥 ERRO CLIMA:", e)

# =====================
# IRRIGAÇÃO
# =====================
def decidir_irrigacao():
    regra = regras[dados["cultura"]]

    if dados["chuva"]:
        return "OFF"

    if dados["solo"] < regra["min"]:
        return "ON"

    if regra["min"] <= dados["solo"] <= regra["max"]:
        if dados["temp"] > 30:
            return "ON"
        return "OFF"

    return "OFF"

# =====================
# MQTT
# =====================
def on_connect(client, userdata, flags, rc):
    print("MQTT conectado:", rc)
    client.subscribe(TOPIC_DADOS)

def on_message(client, userdata, msg):
    global dados

    try:
        payload = json.loads(msg.payload.decode())

        dados["solo"] = payload.get("solo", 0)
        dados["temp"] = payload.get("temp", 0)

        estado = decidir_irrigacao()
        dados["irrigacao"] = estado

        client.publish(TOPIC_CONTROLE, estado)

    except Exception as e:
        print("Erro MQTT:", e)

client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)

client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)

client.on_connect = on_connect
client.on_message = on_message

try:
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()
except Exception as e:
    print("Erro MQTT:", e)

# =====================
# ROTAS
# =====================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dados")
def get_dados():
    global ultima_busca_clima
    agora = time.time()
    
    # 🔥 Verifica se já se passaram 600 segundos (10 minutos) desde a última busca
    if agora - ultima_busca_clima > 600:
        buscar_clima()
        ultima_busca_clima = agora
        
    return jsonify(dados)

@app.route("/cultura", methods=["POST"])
def set_cultura():
    dados["cultura"] = request.json.get("cultura")
    return {"ok": True}

@app.route("/ligar")
def ligar():
    client.publish(TOPIC_CONTROLE, "ON")
    return {"ok": True}

@app.route("/desligar")
def desligar():
    client.publish(TOPIC_CONTROLE, "OFF")
    return {"ok": True}

# =====================
# START
# =====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
