import ssl
from flask import Flask, render_template, jsonify
import paho.mqtt.client as mqtt
import psycopg2
import os

app = Flask(__name__)

# =====================
# MQTT CONFIG
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"
MQTT_TOPIC = "/fazenda/solo/umidade"

# =====================
# BANCO
# =====================
conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS sensores (
    id SERIAL PRIMARY KEY,
    umidade FLOAT,
    irrigacao VARCHAR(10),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()

dados = {"umidade": 0, "irrigacao": "OFF"}

def salvar_dados(umidade, irrigacao):
    cursor.execute(
        "INSERT INTO sensores (umidade, irrigacao) VALUES (%s, %s)",
        (umidade, irrigacao)
    )
    conn.commit()

# =====================
# MQTT CALLBACKS
# =====================
def on_connect(client, userdata, flags, rc):
    print("Conectado ao MQTT:", rc)
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    global dados

    umidade = float(msg.payload.decode())
    dados["umidade"] = umidade

    if umidade < 30:
        dados["irrigacao"] = "ON"
    else:
        dados["irrigacao"] = "OFF"

    salvar_dados(umidade, dados["irrigacao"])
    print("Recebido:", umidade)

# =====================
# MQTT START
# =====================
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)

# 🔥 ESSENCIAL PRA HIVEMQ CLOUD
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
def dados_api():
    return jsonify(dados)

@app.route("/historico")
def historico():
    cursor.execute("SELECT umidade, irrigacao, timestamp FROM sensores ORDER BY timestamp DESC LIMIT 20")
    rows = cursor.fetchall()

    return jsonify([
        {
            "umidade": r[0],
            "irrigacao": r[1],
            "timestamp": r[2].strftime("%H:%M:%S")
        } for r in rows
    ])

# =====================
# START RENDER
# =====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
