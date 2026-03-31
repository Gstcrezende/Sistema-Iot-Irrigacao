import ssl
import json
from flask import Flask, jsonify, request
import paho.mqtt.client as mqtt

app = Flask(__name__)

# =====================
# CONFIG MQTT (HIVEMQ)
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"

TOPIC_DADOS = "/fazenda/dados"
TOPIC_CONTROLE = "/fazenda/irrigacao"

# =====================
# ESTADO
# =====================
dados = {
    "solo": 0,
    "temp": 0,
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
# LÓGICA INTELIGENTE
# =====================
def decidir_irrigacao(solo, temp, cultura):
    regra = regras[cultura]

    # Solo seco
    if solo < regra["min"]:
        return "ON"

    # Solo ok
    if regra["min"] <= solo <= regra["max"]:
        # temperatura alta = precisa mais água
        if temp > 30:
            return "ON"
        return "OFF"

    # Solo muito úmido
    return "OFF"

# =====================
# MQTT CALLBACK
# =====================
def on_connect(client, userdata, flags, rc):
    print("MQTT conectado:", rc)
    client.subscribe(TOPIC_DADOS)

def on_message(client, userdata, msg):
    global dados

    data = json.loads(msg.payload.decode())

    dados["solo"] = data["solo"]
    dados["temp"] = data["temp"]

    cultura = dados["cultura"]

    estado = decidir_irrigacao(dados["solo"], dados["temp"], cultura)
    dados["irrigacao"] = estado

    client.publish(TOPIC_CONTROLE, estado)

    print(dados)

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
# START
# =====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
