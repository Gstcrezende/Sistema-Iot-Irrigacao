import os
import ssl
import json
import requests
import time
import psycopg2
from datetime import timedelta
from flask import Flask, jsonify, request, render_template
import paho.mqtt.client as mqtt
import threading

app = Flask(__name__)

# =====================
# CONFIGURAÇÕES MQTT & API
# =====================
MQTT_HOST = "671be66b88cf41909db655fee73234dd.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "iotuser"
MQTT_PASS = "Iot12345"

API_KEY = "1563f0caa3ed84c078ab47087d40a962"
CIDADE = "Franca,BR"

# =====================
# BANCO DE DADOS (RENDER)
# =====================
DB_URL = "postgresql://database_42u1_user:izGbmDSbHtKdcXlbC5X2pPTBGlfEmoPK@dpg-d75tu5haae7s73cvc45g-a/database_42u1"

def get_db_connection():
    return psycopg2.connect(DB_URL)

def init_db():
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Criação da tabela de dispositivos para suportar múltiplas áreas
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dispositivos (
                id VARCHAR(50) PRIMARY KEY,
                nome VARCHAR(100),
                cultura VARCHAR(50) DEFAULT 'milho',
                modo_controle VARCHAR(10) DEFAULT 'AUTO'
            )
        ''')
        
        # Dispositivo padrão (legado)
        cursor.execute("INSERT INTO dispositivos (id, nome) VALUES ('default', 'Área Principal (Legado)') ON CONFLICT DO NOTHING")
        
        # Criação da tabela de leituras atualizada
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leituras (
                id SERIAL PRIMARY KEY,
                dispositivo_id VARCHAR(50) DEFAULT 'default',
                solo FLOAT,
                temp FLOAT,
                irrigacao VARCHAR(10),
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tenta adicionar a coluna caso a tabela já exista do modelo anterior
        try:
            cursor.execute("ALTER TABLE leituras ADD COLUMN dispositivo_id VARCHAR(50) DEFAULT 'default'")
        except psycopg2.errors.DuplicateColumn:
            pass # Coluna já existe, segue o jogo
        except Exception as e:
            print("Aviso ao alterar tabela:", e)

        cursor.close()
        conn.close()
        print("✅ Banco de dados conectado e esquema atualizado para Multi-Zonas!")
    except Exception as e:
        print("🔥 ERRO NO BANCO DE DADOS:", e)

# =====================
# ESTADO DA APLICAÇÃO (CACHE)
# =====================
estado_clima = {
    "temp_cidade": 0, "cidade": "-", "chuva": False, 
    "previsao_chuva": False, "prob_chuva": 0, "icone": ""
}
ultima_busca_clima = 0

# Dicionário de dispositivos: { id: { nome, cultura, modo_controle, solo, temp, irrigacao, ultima_gravacao } }
dispositivos = {}

regras = {
    "milho": {"min": 40, "max": 70},
    "soja": {"min": 35, "max": 65},
    "cafe": {"min": 45, "max": 75}
}

def carregar_dispositivos():
    global dispositivos
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, nome, cultura, modo_controle FROM dispositivos")
        linhas = cursor.fetchall()
        
        for d in linhas:
            disp_id = d[0]
            dispositivos[disp_id] = {
                "id": disp_id, "nome": d[1], "cultura": d[2], "modo_controle": d[3],
                "solo": 0, "temp": 0, "irrigacao": "OFF", "ultima_gravacao": 0
            }
            
            # Busca última leitura para não iniciar zerado
            cursor.execute("SELECT solo, temp, irrigacao FROM leituras WHERE dispositivo_id = %s ORDER BY id DESC LIMIT 1", (disp_id,))
            leitura = cursor.fetchone()
            if leitura:
                dispositivos[disp_id]["solo"] = leitura[0]
                dispositivos[disp_id]["temp"] = leitura[1]
                dispositivos[disp_id]["irrigacao"] = leitura[2]
                
        cursor.close()
        conn.close()
        print(f"🔄 Carregados {len(dispositivos)} dispositivos do banco.")
    except Exception as e:
        print("🔥 ERRO AO CARREGAR DISPOSITIVOS:", e)

def salvar_leitura(disp_id, solo, temp, irrigacao):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO leituras (dispositivo_id, solo, temp, irrigacao) VALUES (%s, %s, %s, %s)",
            (disp_id, solo, temp, irrigacao)
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print("🔥 ERRO AO SALVAR LEITURA:", e)

# =====================
# CLIMA E PREVISÃO
# =====================
def buscar_clima():
    global estado_clima, ultima_busca_clima
    try:
        url_atual = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": CIDADE, "appid": API_KEY, "units": "metric", "lang": "pt_br"}
        r_atual = requests.get(url_atual, params=params, timeout=5)
        
        url_prev = "https://api.openweathermap.org/data/2.5/forecast"
        r_prev = requests.get(url_prev, params=params, timeout=5)

        if r_atual.status_code == 200:
            data = r_atual.json()
            estado_clima["temp_cidade"] = data["main"]["temp"]
            estado_clima["cidade"] = data["name"]
            estado_clima["icone"] = data["weather"][0]["icon"]
            clima = data["weather"][0]["main"].lower()
            estado_clima["chuva"] = clima in ["rain", "drizzle", "thunderstorm"]
        
        if r_prev.status_code == 200:
            data_prev = r_prev.json()
            vai_chover = False
            prob_max = 0

            for periodo in data_prev["list"][:4]:
                probabilidade = periodo.get("pop", 0) 
                if probabilidade > prob_max: prob_max = probabilidade
                if probabilidade >= 0.6: vai_chover = True

            estado_clima["previsao_chuva"] = vai_chover
            estado_clima["prob_chuva"] = int(prob_max * 100)
            
        ultima_busca_clima = time.time()
    except Exception as e:
        print("🔥 ERRO CLIMA/PREVISÃO:", e)

# =====================
# IRRIGAÇÃO INTELIGENTE
# =====================
def decidir_irrigacao(disp_id):
    disp = dispositivos.get(disp_id)
    if not disp: return "OFF"
    
    modo = disp["modo_controle"]
    if modo == "ON": return "ON"
    if modo == "OFF": return "OFF"
    
    # Modo AUTO
    regra = regras.get(disp["cultura"], regras["milho"])
    if estado_clima["chuva"]: return "OFF"
        
    if estado_clima["previsao_chuva"]:
        if disp["solo"] < (regra["min"] - 10):
            return "ON" 
        else:
            return "OFF" 
            
    if disp["solo"] < regra["min"]: return "ON"
        
    if regra["min"] <= disp["solo"] <= regra["max"]:
        if disp["temp"] > 30: return "ON"
        return "OFF"
        
    return "OFF"

# =====================
# MQTT
# =====================
def on_connect(client, userdata, flags, rc):
    print("MQTT conectado:", rc)
    # Assina todos os tópicos da fazenda para pegar múltiplos dispositivos
    client.subscribe("/fazenda/#")

def on_message(client, userdata, msg):
    global dispositivos
    try:
        parts = msg.topic.split('/')
        disp_id = None
        
        # Identifica de qual dispositivo veio a mensagem
        if len(parts) == 3 and parts[2] == 'dados': # /fazenda/dados (Legado)
            disp_id = 'default'
        elif len(parts) == 4 and parts[3] == 'dados': # /fazenda/id/dados (Novo modelo)
            disp_id = parts[2]
            
        if not disp_id: return
        
        # Auto-registro se o dispositivo for novo
        if disp_id not in dispositivos:
            nome_novo = f"Nova Área ({disp_id})"
            dispositivos[disp_id] = {
                "id": disp_id, "nome": nome_novo, "cultura": "milho", "modo_controle": "AUTO",
                "solo": 0, "temp": 0, "irrigacao": "OFF", "ultima_gravacao": 0
            }
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO dispositivos (id, nome) VALUES (%s, %s) ON CONFLICT DO NOTHING", (disp_id, nome_novo))
            conn.commit()
            cursor.close()
            conn.close()

        payload = json.loads(msg.payload.decode())
        disp = dispositivos[disp_id]
        disp["solo"] = payload.get("solo", 0)
        disp["temp"] = payload.get("temp", 0)

        estado = decidir_irrigacao(disp_id)
        disp["irrigacao"] = estado
        
        # Publica no tópico correto da válvula
        topico_controle = "/fazenda/irrigacao" if disp_id == 'default' else f"/fazenda/{disp_id}/irrigacao"
        client.publish(topico_controle, estado)

        agora = time.time()
        # Grava no banco a cada 5 minutos por dispositivo
        if agora - disp.get("ultima_gravacao", 0) > 300:
            salvar_leitura(disp_id, disp["solo"], disp["temp"], estado)
            disp["ultima_gravacao"] = agora

    except Exception as e:
        print("Erro MQTT on_message:", e)

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
# ROTAS API E FRONTEND
# =====================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/estado")
def get_estado():
    agora = time.time()
    if agora - ultima_busca_clima > 300:
        buscar_clima()
        
    return jsonify({
        "clima": estado_clima,
        "dispositivos": list(dispositivos.values())
    })

@app.route("/api/dispositivo", methods=["POST"])
def add_dispositivo():
    dados = request.json
    disp_id = dados.get("id")
    nome = dados.get("nome")
    cultura = dados.get("cultura", "milho")
    
    if disp_id and nome:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO dispositivos (id, nome, cultura) VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome, cultura = EXCLUDED.cultura", (disp_id, nome, cultura))
            conn.commit()
            cursor.close()
            conn.close()
            
            if disp_id not in dispositivos:
                dispositivos[disp_id] = {
                    "id": disp_id, "nome": nome, "cultura": cultura, "modo_controle": "AUTO",
                    "solo": 0, "temp": 0, "irrigacao": "OFF", "ultima_gravacao": 0
                }
            else:
                dispositivos[disp_id]["nome"] = nome
                dispositivos[disp_id]["cultura"] = cultura
                
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Dados inválidos"}), 400

@app.route("/api/dispositivo/<disp_id>", methods=["DELETE"])
def remove_dispositivo(disp_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM dispositivos WHERE id = %s", (disp_id,))
        conn.commit()
        cursor.close()
        conn.close()
        
        if disp_id in dispositivos:
            del dispositivos[disp_id]
            
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/dispositivo/<disp_id>/config", methods=["POST"])
def config_dispositivo(disp_id):
    if disp_id not in dispositivos:
        return jsonify({"error": "Não encontrado"}), 404
        
    dados = request.json
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if "cultura" in dados:
            cultura = dados["cultura"]
            cursor.execute("UPDATE dispositivos SET cultura = %s WHERE id = %s", (cultura, disp_id))
            dispositivos[disp_id]["cultura"] = cultura
            
        if "modo" in dados:
            modo = dados["modo"] # AUTO, ON, OFF
            cursor.execute("UPDATE dispositivos SET modo_controle = %s WHERE id = %s", (modo, disp_id))
            dispositivos[disp_id]["modo_controle"] = modo
            
            # Força envio MQTT imediato se for manual
            if modo in ["ON", "OFF"]:
                topico_controle = "/fazenda/irrigacao" if disp_id == 'default' else f"/fazenda/{disp_id}/irrigacao"
                client.publish(topico_controle, modo)
                dispositivos[disp_id]["irrigacao"] = modo
                salvar_leitura(disp_id, dispositivos[disp_id]["solo"], dispositivos[disp_id]["temp"], modo)
            
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/historico/<disp_id>")
def get_historico(disp_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT solo, irrigacao, data_hora FROM leituras WHERE dispositivo_id = %s ORDER BY id DESC LIMIT 60", (disp_id,))
        linhas = cursor.fetchall()
        cursor.close()
        conn.close()

        linhas.reverse() 
        historico = {"labels": [], "data": [], "status": []}
        for linha in linhas:
            historico["data"].append(linha[0])
            historico["status"].append(linha[1]) 
            hora_local = linha[2] - timedelta(hours=3)
            historico["labels"].append(hora_local.strftime("%H:%M")) 

        return jsonify(historico)
    except Exception as e:
        print("Erro ao buscar histórico:", e)
        return jsonify({"labels": [], "data": [], "status": []})

# =====================
# START
# =====================
if __name__ == "__main__":
    init_db()
    carregar_dispositivos()
    buscar_clima() # Primeira busca
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
