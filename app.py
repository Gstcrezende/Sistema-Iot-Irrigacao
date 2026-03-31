import os
import ssl
import json
import requests
import time
import psycopg2
from datetime import timedelta
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
            print(f"🔄 Último estado: Solo {linha[0]}% | Temp {linha[1]}°C")
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
    "previsao_chuva": False, 
    "prob_chuva": 0,         
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
# CLIMA E PREVISÃO
# =====================
def buscar_clima():
    global dados
    try:
        url_atual = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": CIDADE, "appid": API_KEY, "units": "metric", "lang": "pt_br"}
        r_atual = requests.get(url_atual, params=params, timeout=5)
        
        url_prev = "https://api.openweathermap.org/data/2.5/forecast"
        r_prev = requests.get(url_prev, params=params, timeout=5)

        if r_atual.status_code == 200:
            data = r_atual.json()
            dados["temp_cidade"] = data["main"]["temp"]
            dados["cidade"] = data["name"]
            dados["icone"] = data["weather"][0]["icon"]
            clima = data["weather"][0]["main"].lower()
            dados["chuva"] = clima in ["rain", "drizzle", "thunderstorm"]
        
        if r_prev.status_code == 200:
            data_prev = r_prev.json()
            vai_chover = False
            prob_max = 0

            for periodo in data_prev["list"][:4]:
                probabilidade = periodo.get("pop", 0) 
                if probabilidade > prob_max: prob_max = probabilidade
                if probabilidade >= 0.6: vai_chover = True

            dados["previsao_chuva"] = vai_chover
            dados["prob_chuva"] = int(prob_max * 100) 
            print(f"✅ Previsão: Chance de chuva (12h): {dados['prob_chuva']}%")

    except Exception as e:
        print("🔥 ERRO CLIMA/PREVISÃO:", e)

# =====================
# IRRIGAÇÃO INTELIGENTE
# =====================
def decidir_irrigacao():
    regra = regras[dados["cultura"]]
    
    if dados["chuva"]: return "OFF"
        
    if dados["previsao_chuva"]:
        if dados["solo"] < (regra["min"] - 10):
            return "ON" 
        else:
            return "OFF" 
            
    if dados["solo"] < regra["min"]: return "ON"
        
    if regra["min"] <= dados["solo"] <= regra["max"]:
        if dados["temp"] > 30: return "ON"
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
    if agora - ultima_busca_clima > 300:
        buscar_clima()
        ultima_busca_clima = agora
    return jsonify(dados)

@app.route("/historico")
def get_historico():
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        # 🔥 Agora buscamos também a coluna "irrigacao" do banco de dados
        cursor.execute("SELECT solo, irrigacao, data_hora FROM leituras ORDER BY id DESC LIMIT 60")
        linhas = cursor.fetchall()
        cursor.close()
        conn.close()

        linhas.reverse() 

        historico = {"labels": [], "data": [], "status": []}
        for linha in linhas:
            historico["data"].append(linha[0])
            historico["status"].append(linha[1]) # Guarda se estava ON ou OFF naquele momento
            
            # 🔥 Correção do fuso horário (-3 horas para o Brasil)
            hora_local = linha[2] - timedelta(hours=3)
            historico["labels"].append(hora_local.strftime("%H:%M")) 

        return jsonify(historico)
    except Exception as e:
        print("Erro ao buscar histórico:", e)
        return jsonify({"labels": [], "data": [], "status": []})

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
    carregar_ultimo_estado()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
