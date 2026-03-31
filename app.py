from flask import Flask, render_template, jsonify
import paho.mqtt.client as mqtt
import psycopg2
import os
import threading

app = Flask(__name__)

# =====================
# BANCO DE DADOS
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

# =====================
# DADOS EM MEMÓRIA
# =====================
dados = {
    "umidade": 0,
    "irrigacao": "OFF"
}
dados_lock = threading.Lock()

# =====================
# SALVAR NO BANCO
# =====================
def salvar_dados(umidade, irrigacao):
    cursor.execute(
        "INSERT INTO sensores (umidade, irrigacao) VALUES (%s, %s)",
        (umidade, irrigacao)
    )
    conn.commit()

# =====================
# MQTT
# =====================
def on_message(client, userdata, msg):
    global dados

    if msg.topic == "/fazenda/solo/umidade":
        umidade = float(msg.payload.decode())
        irrigacao = "ON" if umidade < 30 else "OFF"

        with dados_lock:
            dados["umidade"] = umidade
            dados["irrigacao"] = irrigacao

        salvar_dados(umidade, irrigacao)

client = mqtt.Client(client_id=f"agrotech-{os.getpid()}")
try:
    client.connect("broker.hivemq.com", 1883)
    client.subscribe("/fazenda/solo/umidade")
    client.on_message = on_message
    client.loop_start()
except Exception as e:
    print(f"MQTT connection failed: {e}")

# =====================
# ROTAS
# =====================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dados")
def get_dados():
    with dados_lock:
        snapshot = dict(dados)
    return jsonify(snapshot)

@app.route("/historico")
def historico():
    cursor.execute(
        "SELECT id, umidade, irrigacao, timestamp FROM sensores ORDER BY timestamp DESC LIMIT 20"
    )
    rows = cursor.fetchall()

    return jsonify([
        {
            "umidade": r[1],
            "irrigacao": r[2],
            "timestamp": r[3].strftime("%H:%M:%S")
        } for r in rows
    ])

# =====================
# START
# =====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
