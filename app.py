import os
import ssl
import json
import requests
import time
import psycopg2
from flask import Flask, jsonify, request, render_template
import paho.mqtt.client as mqtt

app = Flask(__name__)

# =====================
# CONFIGURAÇÕES MQTT & API
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"

TOPIC_DADOS = "/fazenda/dados"
TOPIC_CONTROLE = "/fazenda/irrigacao"

API_KEY = "1563f0caa3ed84c078ab47087d40a962"
CIDADE = "Franca,BR"

# =====================
# BANCO DE DADOS (RENDER)
# =====================
DB_URL = "postgresql://database_42u1_user:izGbmDSbHtKdcXlbC5X2pPTBGlfEmoPK@dpg-d75tu5haae7s73cvc45g-a/database_42u1"

def init_db():
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leituras (
                id SERIAL PRIMARY KEY,
                solo FLOAT,
                temp FLOAT,
                irrigacao VARCHAR(10),
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Banco de dados conectado e tabela verificada!")
    except Exception as e:
        print("🔥 ERRO NO BANCO DE DADOS:", e)

def carregar_ultimo_estado():
    """Busca a última leitura do banco para não iniciar com zero"""
    global dados
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT solo, temp, irrigacao FROM leituras ORDER BY id DESC LIMIT 1")
        linha = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if linha:
            dados["solo"] = linha[0]
            dados["temp"] = linha[1]
            dados["irrigacao"] = linha[2]
            print(f"🔄 Último estado recuperado: Solo {linha[0]}% | Temp {linha[1]}°C")
    except Exception as e:
        print("🔥 ERRO AO CARREGAR ÚLTIMO ESTADO:", e)

def salvar_no_banco(solo, temp, irrigacao):
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO leituras (solo, temp, irrigacao) VALUES (%s, %s, %s)",
            (solo, temp, irrigacao)
        )
        conn.commit()
        cursor.close()
        conn.close()
        print("💾 Dado salvo no PostgreSQL com sucesso!")
    except Exception as e:
        print("🔥 ERRO AO SALVAR NO BANCO:", e)

# =====================
# ESTADO DA APLICAÇÃO
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

ultima_busca_clima = 0
ultima_gravacao_db = 0

# =====================
# CLIMA E IRRIGAÇÃO
# =====================
def buscar_clima():
    global dados
    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": CIDADE, "appid": API_KEY, "units": "metric", "lang": "pt_br"}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()

        if r.status_code == 200:
            dados["temp_cidade"] = data["main"]["temp"]
            dados["cidade"] = data["name"]
            dados["icone"] = data["weather"][0]["icon"]
            clima = data["weather"][0]["main"].lower()
            dados["chuva"] = clima in ["rain", "drizzle", "thunderstorm"]
            print("✅ Clima atualizado!")
    except Exception as e:
        print("🔥 ERRO CLIMA:", e)

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
    global dados, ultima_gravacao_db
    try:
        payload = json.loads(msg.payload.decode())
        dados["solo"] = payload.get("solo", 0)
        dados["temp"] = payload.get("temp", 0)

        estado = decidir_irrigacao()
        dados["irrigacao"] = estado
        client.publish(TOPIC_CONTROLE, estado)

        # Salva no banco a cada 5 minutos (300 segundos)
        agora = time.time()
        if agora - ultima_gravacao_db > 300:
            salvar_no_banco(dados["solo"], dados["temp"], estado)
            ultima_gravacao_db = agora

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
    # Busca clima a cada 20 minutos (1200 seg)
    if agora - ultima_busca_clima > 1200:
        buscar_clima()
        ultima_busca_clima = agora
    return jsonify(dados)

@app.route("/historico")
def get_historico():
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        # Pega as últimas 15 leituras do banco
        cursor.execute("SELECT solo, data_hora FROM leituras ORDER BY id DESC LIMIT 15")
        linhas = cursor.fetchall()
        cursor.close()
        conn.close()

        linhas.reverse() # Inverte para o gráfico ficar na ordem cronológica (esq -> dir)

        historico = {"labels": [], "data": []}
        for linha in linhas:
            historico["data"].append(linha[0])
            historico["labels"].append(linha[1].strftime("%H:%M:%S"))

        return jsonify(historico)
    except Exception as e:
        print("Erro ao buscar histórico:", e)
        return jsonify({"labels": [], "data": []})

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
    init_db()
    carregar_ultimo_estado() # Puxa a última memória salva antes de subir o app
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
