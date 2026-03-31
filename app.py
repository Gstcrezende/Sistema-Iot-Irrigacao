import os
import ssl
import json
import requests
from flask import Flask, jsonify, request, render_template
import paho.mqtt.client as mqtt

app = Flask(__name__)

# =====================
# CONFIG MQTT
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"

TOPIC_DADOS = "/fazenda/dados"
TOPIC_CONTROLE = "/fazenda/irrigacao"

# =====================
# CLIMA
# =====================
API_KEY = "1563f0caa3ed84c078ab47087d40a962"
CIDADE = "Ribeirao Preto,BR"

# =====================
# ESTADO
# =====================
dados = {
    "solo": 0,
    "temp": 0,
    "temp_cidade": 0,
    "chuva": False,
    "cultura": "milho",
    "irrigacao": "OFF"
}

# =====================
# REGRAS POR CULTURA
# =====================
regras = {
    "milho": {"min": 40, "max": 70},
    "soja": {"min": 35, "max": 65},
    "cafe": {"min": 45, "max": 75}
}

# =====================
# CLIMA REAL
# =====================
def verificar_clima():
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={CIDADE}&appid={API_KEY}&units=metric"
        r = requests.get(url, timeout=5)
        data = r.json()

        print("Resposta API:", data)

        if "main" not in data:
            return False, 0

        clima = data["weather"][0]["main"].lower()
        temp_cidade = data["main"]["temp"]

        chuva = clima in ["rain", "drizzle", "thunderstorm"]

        return chuva, temp_cidade

    except Exception as e:
        print("Erro clima:", e)
        return False, 0
# =====================
# LÓGICA INTELIGENTE
# =====================
def decidir_irrigacao(solo, temp, cultura, chuva):
    regra = regras[cultura]

    if chuva:
        return "OFF"

    if solo < regra["min"]:
        return "ON"

    if regra["min"] <= solo <= regra["max"]:
        if temp > 30:
            return "ON"
        return "OFF"

    return "OFF"

# =====================
# MQTT CALLBACK
# =====================
def on_connect(client, userdata, flags, rc):
    print("MQTT conectado:", rc)
    client.subscribe(TOPIC_DADOS)

def on_message(client, userdata, msg):
    global dados

    try:
        data = json.loads(msg.payload.decode())

        dados["solo"] = data.get("solo", 0)
        dados["temp"] = data.get("temp", 0)

        chuva, temp_cidade = verificar_clima()

        dados["chuva"] = chuva
        dados["temp_cidade"] = temp_cidade

        estado = decidir_irrigacao(
            dados["solo"],
            dados["temp"],
            dados["cultura"],
            dados["chuva"]
        )

        dados["irrigacao"] = estado

        client.publish(TOPIC_CONTROLE, estado)

        print(dados)

    except Exception as e:
        print("Erro MQTT:", e)

# =====================
# MQTT START
# =====================
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)

client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)

client.on_connect = on_connect
client.on_message = on_message

client.connect(MQTT_HOST, MQTT_PORT)
client.loop_start()

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
    dados["cultura"] = request.json["cultura"]
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
# START (RENDER)
# =====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
