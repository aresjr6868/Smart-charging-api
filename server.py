import os, json, copy, logging
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "server_data.json")
LOG_FILE  = os.path.join(BASE_DIR, "sync_log.jsonl")
DASH_FILE = os.path.join(BASE_DIR, "dashboard.html")
PORT      = int(os.environ.get("PORT", 5000))

DEFAULT_STATION_DATA = {
    "electrical": {
        "voltage_v": 420.0,
        "current_a": 60.0,
        "power_kw": 25.2,
        "energy_total_kwh": 1300.0,
        "frequency_hz": 50.0
    },
    "thermal": {
        "connector_temp_c": 35.0,
        "inverter_temp_c": 40.0,
        "ambient_temp_c": 30.0,
        "cooling_fan_speed_rpm": 1200
    },
    "session": {
        "status": "CHARGING",
        "vehicle_soc": 80,
        "session_duration_sec": 900,
        "est_time_remaining_sec": 1800
    },
    "type_connect": "type_connect_2"
}

app = Flask(__name__)
CORS(app)  # Cho phep cac ung dung Unity / Web / Mobile tu domain khac ket noi
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def clean_dict(data):
    """Strip out any internal metadata keys starting with '_' recursively"""
    if isinstance(data, dict):
        return {k: clean_dict(v) for k, v in data.items() if not str(k).startswith("_")}
    elif isinstance(data, list):
        return [clean_dict(item) for item in data]
    return data

def deep_update(base, updates):
    """Recursively merge updates dictionary into base dictionary"""
    if not isinstance(base, dict) or not isinstance(updates, dict):
        return updates
    for k, v in updates.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            base[k] = deep_update(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base

def load_raw_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logging.warning("[LOAD ERROR] %s", e)
    return copy.deepcopy(DEFAULT_STATION_DATA)

def save_raw_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def append_log(entry):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def read_logs(limit=50):
    if not os.path.exists(LOG_FILE):
        return []
    lines = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                try:
                    lines.append(json.loads(s))
                except Exception:
                    pass
    return lines[-limit:][::-1]

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for Render monitoring"""
    return jsonify({"status": "healthy", "service": "char-server"}), 200

@app.route("/api/products", methods=["GET"])
def get_products():
    """Endpoint tuong thich theo huong dan flask.md"""
    raw_server = load_raw_data()
    return jsonify({
        "success": True,
        "message": "Lay danh sach thanh cong",
        "data": clean_dict(raw_server)
    }), 200

@app.route("/api/sync", methods=["POST"])
def sync():
    try:
        client_data = request.get_json(force=True, silent=True)
        if client_data is None:
            return jsonify({"error": "Invalid JSON body"}), 400

        clean_client = clean_dict(client_data)
        raw_server = load_raw_data()

        last_modified = raw_server.get("_last_modified")
        last_sync = raw_server.get("_last_sync")

        # If server was manually modified via Dashboard / PUT /api/data more recently than last sync,
        # server state takes precedence so Unity can receive the latest server changes.
        # Otherwise, client (Unity) data is adopted and stored into server.
        if last_modified and (not last_sync or last_modified > last_sync):
            logging.info("[SYNC] Server has newer edits -> sending updated server data to Unity")
        else:
            raw_server = deep_update(raw_server, clean_client)

        now_iso = datetime.now().isoformat()
        raw_server["_last_sync"] = now_iso
        save_raw_data(raw_server)

        # Always return the exact, clean station JSON format without any internal metadata keys
        clean_response = clean_dict(raw_server)

        append_log({
            "time": now_iso,
            "action": "sync",
            "client": request.remote_addr,
            "payload": clean_response
        })
        logging.info("[SYNC OK] Response clean JSON (%d keys)", len(clean_response))
        return jsonify(clean_response), 200

    except Exception as e:
        logging.error("[SYNC ERROR] %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/data", methods=["GET"])
def get_data():
    """Returns pure station JSON data matching the station schema"""
    return jsonify(clean_dict(load_raw_data())), 200

@app.route("/api/data", methods=["PUT", "PATCH"])
def update_data():
    """Manual update from Dashboard or External API"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "Invalid JSON"}), 400

    clean_body = clean_dict(body)
    raw_server = load_raw_data()
    raw_server = deep_update(raw_server, clean_body)
    now_iso = datetime.now().isoformat()
    raw_server["_last_modified"] = now_iso
    save_raw_data(raw_server)

    clean_saved = clean_dict(raw_server)
    append_log({"time": now_iso, "action": "manual_update", "payload": clean_body})
    logging.info("[UPDATE OK] Server data updated manually via API/Dashboard")
    return jsonify({"status": "ok", "data": clean_saved}), 200

@app.route("/api/data", methods=["DELETE"])
def clear_data():
    """Reset to clean default station template"""
    default_data = copy.deepcopy(DEFAULT_STATION_DATA)
    save_raw_data(default_data)
    append_log({"time": datetime.now().isoformat(), "action": "clear"})
    return jsonify({"status": "reset_to_default", "data": default_data}), 200

@app.route("/api/logs", methods=["GET"])
def get_logs():
    limit = int(request.args.get("limit", 50))
    return jsonify(read_logs(limit)), 200

@app.route("/api/status", methods=["GET"])
def status():
    raw_server = load_raw_data()
    clean_server = clean_dict(raw_server)
    count = 0
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8") as lf:
            count = sum(1 for _ in lf)
    return jsonify({
        "status": "running",
        "server_time": datetime.now().isoformat(),
        "last_sync": raw_server.get("_last_sync"),
        "last_modified": raw_server.get("_last_modified"),
        "data_keys": list(clean_server.keys()),
        "log_entries": count
    }), 200

@app.route("/", methods=["GET"])
def dashboard():
    if os.path.exists(DASH_FILE):
        with open(DASH_FILE, encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
    return "<h2>Dashboard not found. Place dashboard.html next to server.py</h2>", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", PORT))
    print("=" * 55)
    print("  CharServer - Clean Unity API Bridge")
    print("=" * 55)
    print("  Port      : %d" % port)
    print("  Dashboard : http://localhost:%d/" % port)
    print("  Sync URL  : http://localhost:%d/api/sync" % port)
    print("  Data URL  : http://localhost:%d/api/data" % port)
    print("  Logs URL  : http://localhost:%d/api/logs" % port)
    print("  Health URL: http://localhost:%d/health" % port)
    print("=" * 55)
    app.run(host="0.0.0.0", port=port, debug=False)
