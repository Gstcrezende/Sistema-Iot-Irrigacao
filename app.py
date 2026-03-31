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
# CONFIGURAÇÕES
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"

TOPIC_DADOS = "/fazenda/dados"
TOPIC_CONTROLE = "/fazenda/irrigacao"

API_KEY = "1563f0caa3ed84c078ab47087d40a962"
CIDADE = "RibeiraoPreto,BR"

# =====================
# ESTADO GLOBAL
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

# =====================
# CLIMA (COM DEBUG COMPLETO)
# =====================
def buscar_clima_api():
    try:
        url = "https://api.openweathermap.org/data/2.5/weather"

        params = {
            "q": CIDADE,
            "appid": API_KEY,
            "units": "metric",
            "lang": "pt_br"
        }

        print("\n🔎 Buscando clima...")
        print("Cidade enviada:", CIDADE)

        response = requests.get(url, params=params, timeout=10)

        print("Status HTTP:", response.status_code)
        print("Resposta RAW:", response.text)

        data = response.json()

        if response.status_code != 200:
            print("❌ Erro API:", data.get("message"))
            return False, False, 0, "-", "", 0, 0

        temp = data["main"]["temp"]
        clima_main = data["weather"][0]["main"].lower()
        descricao = data["weather"][0]["description"]
        icon = data["weather"][0]["icon"]

        cidade_nome = data["name"]
        lat = data["coord"]["lat"]
        lon = data["coord"]["lon"]

        chuva = any(x in clima_main for x in ["rain", "drizzle", "thunderstorm"])

        print(f"✅ Cidade: {cidade_nome}")
        print(f"🌡️ Temp: {temp}")
        print(f"🌧️ Chuva: {chuva}")
        print(f"📍 Lat/Lon: {lat}, {lon}")
        print(f"🖼️ Ícone: {icon}")

        return True, chuva, temp, cidade_nome, icon, lat, lon

    except Exception as e:
        print(f"🔥 ERRO CRÍTICO clima: {e}")
        return False, False, 0, "-", "", 0, 0

def tarefa_atualizar_clima():
    global dados
    while True:
        sucesso, chuva, temp_cidade, cidade_nome, icon, lat, lon = buscar_clima_api()

        if sucesso:
            dados["chuva"] = chuva
            dados["temp_cidade"] = temp_cidade
            dados["cidade"] = cidade_nome
            dados["icone"] = icon
            dados["lat"] = lat
            dados["lon"] = lon

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
        if temp_sensor > 30:
            return "ON"
        return "OFF"

    return "OFF"

# =====================
# MQTT CALLBACKS
# =====================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Conectado ao HiveMQ!")
        client.subscribe(TOPIC_DADOS)
    else:
        print("Erro MQTT:", rc)

def on_message(client, userdata, msg):
    global dados
    try:
        payload = json.loads(msg.payload.decode())

        dados["solo"] = payload.get("solo", 0)
        dados["temp"] = payload.get("temp", 0)

        estado = decidir_irrigacao(
            dados["solo"],
            dados["temp"],
            dados["cultura"],
            dados["chuva"]
        )

        dados["irrigacao"] = estado
        client.publish(TOPIC_CONTROLE, estado)

    except Exception as e:
        print("Erro MQTT:", e)

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
# ROTAS
# =====================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dados")
def get_dados():
    return jsonify(dados)

@app.route("/cultura", methods=["POST"])
def set_cultura():
    cultura = request.json.get("cultura")
    if cultura in regras:
        dados["cultura"] = cultura
        return {"ok": True}
    return {"ok": False}

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
# START
# =====================
if __name__ == "__main__":
    t = threading.Thread(target=tarefa_atualizar_clima, daemon=True)
    t.start()

    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
