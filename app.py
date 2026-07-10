import os
import io
import csv
import sqlite3
import requests
from datetime import datetime, timedelta
from flask import Flask, Response, render_template_string, request, g
from dotenv import load_dotenv

# ── Carga automática del .env (si existe) ───────────────────────────
load_dotenv()

GOVEE_API_KEY = os.getenv("GOVEE_API_KEY")
BASE_URL      = "https://developer-api.govee.com/v1"
DB_PATH       = os.getenv("DB_PATH", "govee_history.db")

app = Flask(__name__)

# ─────────────────────────────────────────────
# DATABASE  (historial de lecturas)
# ─────────────────────────────────────────────
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_database", None)
    if db:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            gateway     TEXT    NOT NULL,
            device_mac  TEXT    NOT NULL,
            device_name TEXT,
            model       TEXT,
            online      INTEGER,
            power_state TEXT,
            brightness  INTEGER,
            temperature REAL,
            humidity    REAL,
            recorded_at TEXT    NOT NULL
        )
    """)
    db.commit()
    db.close()

init_db()

# ─────────────────────────────────────────────
# GOVEE API HELPERS
# ─────────────────────────────────────────────
def govee_get(path, params=None):
    key = os.getenv("GOVEE_API_KEY")  # re-read en cada llamada por si cargó tarde
    if not key:
        raise RuntimeError("Missing GOVEE_API_KEY. Verifica tu archivo .env")
    headers = {"Govee-API-Key": key}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params or {})
    resp.raise_for_status()
    return resp.json()

def extract_sensor_data(raw_state):
    temperature = humidity = None
    try:
        props = raw_state.get("data", {}).get("properties", [])
        for prop in props:
            if "temperature" in prop:
                v = prop["temperature"]
                temperature = v / 100 if v > 1000 else v
            if "humidity" in prop:
                v = prop["humidity"]
                humidity = v / 100 if v > 1000 else v
    except Exception:
        pass
    if temperature is None:
        try:
            v = raw_state.get("temperature") or raw_state.get("data", {}).get("temperature")
            if v is not None:
                temperature = v / 100 if v > 1000 else v
        except Exception:
            pass
    if humidity is None:
        try:
            v = raw_state.get("humidity") or raw_state.get("data", {}).get("humidity")
            if v is not None:
                humidity = v / 100 if v > 1000 else v
        except Exception:
            pass
    return temperature, humidity

def fetch_and_store(gateway: str):
    devices_resp = govee_get("/devices")
    raw_devices  = devices_resp.get("data", {}).get("devices", [])
    now          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db           = get_db()
    results      = []

    for d in raw_devices:
        mac   = d.get("device", "")
        model = d.get("model", "")
        name  = d.get("deviceName", "Unnamed")

        gw_map = _parse_gateway_map()
        if gw_map and gateway != "all":
            prefix = gw_map.get(gateway, "")
            if prefix and not mac.startswith(prefix):
                continue

        state_resp            = govee_get("/devices", params={"device": mac, "model": model})
        temperature, humidity = extract_sensor_data(state_resp)
        props                 = state_resp.get("data", {}).get("properties", [])
        online                = int(state_resp.get("data", {}).get("online", False))
        power_state = brightness = None
        for prop in props:
            if "powerSwitch" in prop:
                power_state = "on" if prop["powerSwitch"] == 1 else "off"
            if "brightness" in prop:
                brightness = prop["brightness"]

        db.execute("""
            INSERT INTO readings
              (gateway, device_mac, device_name, model, online, power_state,
               brightness, temperature, humidity, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (gateway, mac, name, model, online, power_state,
               brightness, temperature, humidity, now))

        results.append({
            "name": name, "device": mac, "model": model,
            "online": bool(online), "power_state": power_state,
            "brightness": brightness, "temperature": temperature,
            "humidity": humidity, "recorded_at": now,
        })

    db.commit()
    return results

def _parse_gateway_map():
    raw = os.getenv("GATEWAY_MAP", "")
    result = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":", 1)
        if len(parts) == 2:
            result[parts[0].strip()] = parts[1].strip()
    return result

def query_history(gateway, date_from, date_to, device_mac):
    db     = get_db()
    sql    = "SELECT * FROM readings WHERE 1=1"
    params = []
    if gateway and gateway != "all":
        sql += " AND gateway = ?"
        params.append(gateway)
    if date_from:
        sql += " AND recorded_at >= ?"
        params.append(date_from + " 00:00:00")
    if date_to:
        sql += " AND recorded_at <= ?"
        params.append(date_to + " 23:59:59")
    if device_mac and device_mac != "all":
        sql += " AND device_mac = ?"
        params.append(device_mac)
    sql += " ORDER BY recorded_at DESC LIMIT 500"
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

def get_known_gateways():
    db   = get_db()
    rows = db.execute("SELECT DISTINCT gateway FROM readings ORDER BY gateway").fetchall()
    return [r["gateway"] for r in rows] or ["all"]

def get_known_devices(gateway):
    db = get_db()
    if gateway and gateway != "all":
        rows = db.execute(
            "SELECT DISTINCT device_mac, device_name FROM readings WHERE gateway=? ORDER BY device_name",
            (gateway,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT DISTINCT device_mac, device_name FROM readings ORDER BY device_name"
        ).fetchall()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────
HTML_TEMPLATE = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Govee &mdash; Monitor de Dispositivos</title>
  <style>
    :root{
      --bg:#0f172a;--surface:#1e293b;--border:#334155;
      --blue:#3b82f6;--cyan:#06b6d4;--orange:#f97316;
      --green:#22c55e;--red:#ef4444;--yellow:#eab308;
      --text:#f1f5f9;--muted:#94a3b8;
    }
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:2rem 1.5rem}
    header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem;
           margin-bottom:1.75rem;padding-bottom:1.25rem;border-bottom:1px solid var(--border)}
    header h1{font-size:1.4rem;font-weight:700;display:flex;align-items:center;gap:.5rem}
    .badge{font-size:.65rem;background:var(--blue);color:#fff;padding:.2rem .55rem;border-radius:999px;font-weight:700}
    .live-dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite;display:inline-block}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
    .filter-bar{background:var(--surface);border:1px solid var(--border);border-radius:14px;
      padding:1.25rem 1.5rem;margin-bottom:1.75rem;display:flex;flex-wrap:wrap;gap:1rem;align-items:flex-end}
    .field{display:flex;flex-direction:column;gap:.3rem;min-width:160px;flex:1}
    .field label{font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
    .field select,.field input{background:var(--bg);color:var(--text);border:1px solid var(--border);
      border-radius:8px;padding:.45rem .75rem;font-size:.85rem;outline:none}
    .field select:focus,.field input:focus{border-color:var(--blue)}
    .btn{display:inline-flex;align-items:center;gap:.35rem;padding:.5rem 1.1rem;
         border-radius:8px;font-size:.85rem;font-weight:600;text-decoration:none;
         border:none;cursor:pointer;transition:opacity .2s}
    .btn:hover{opacity:.82}
    .btn-primary{background:var(--blue);color:#fff}
    .btn-outline{background:transparent;color:var(--text);border:1px solid var(--border)}
    .stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:1rem;margin-bottom:1.75rem}
    .stat-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1.1rem 1rem}
    .stat-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:.3rem}
    .stat-value{font-size:1.9rem;font-weight:800;line-height:1}
    .c-blue{color:var(--blue)}.c-green{color:var(--green)}.c-yellow{color:var(--yellow)}
    .c-muted{color:var(--muted);font-size:.95rem;font-weight:500}
    .table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:14px;margin-top:.5rem}
    table{border-collapse:collapse;width:100%;min-width:900px}
    thead{background:var(--surface)}
    th,td{padding:.7rem 1rem;text-align:left;font-size:.82rem;border-bottom:1px solid var(--border)}
    th{color:var(--muted);font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em}
    tr:last-child td{border-bottom:none}
    tr:hover td{background:rgba(255,255,255,.025)}
    .pill{display:inline-block;padding:.15rem .5rem;border-radius:999px;font-size:.7rem;font-weight:700}
    .p-online{background:rgba(34,197,94,.15);color:var(--green)}
    .p-offline{background:rgba(239,68,68,.15);color:var(--red)}
    .p-on{background:rgba(234,179,8,.15);color:var(--yellow)}
    .p-off{background:rgba(148,163,184,.1);color:var(--muted)}
    .v-temp{color:var(--orange);font-weight:700}
    .v-hum{color:var(--cyan);font-weight:700}
    .v-blue{color:var(--blue);font-weight:600}
    .na{color:var(--muted);font-style:italic}
    .actions{display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1rem}
    .footer-note{margin-top:1.5rem;font-size:.72rem;color:var(--muted);text-align:center}
    /* ERROR BANNER */
    .error-banner{background:rgba(239,68,68,.15);border:1px solid var(--red);color:var(--red);
      border-radius:10px;padding:1rem 1.25rem;margin-bottom:1.5rem;font-size:.85rem}
  </style>
</head>
<body>
<header>
  <h1>&#127774; Govee Monitor <span class="badge">LIVE</span></h1>
  <span style="font-size:.8rem;color:var(--muted);display:flex;align-items:center;gap:.4rem">
    <span class="live-dot"></span> Auto-actualizaci&oacute;n cada 30 s
  </span>
</header>

{% if error %}
<div class="error-banner">
  <strong>&#9888; Error al conectar con Govee API:</strong> {{ error }}
  <br><small>Verifica que <code>GOVEE_API_KEY</code> est&eacute; definida en tu archivo <code>.env</code></small>
</div>
{% endif %}

<form method="GET" action="/" class="filter-bar">
  <div class="field">
    <label>Gateway</label>
    <select name="gateway">
      <option value="all" {% if selected_gateway=='all' %}selected{% endif %}>Todos</option>
      {% for gw in gateways %}
        <option value="{{ gw }}" {% if gw==selected_gateway %}selected{% endif %}>{{ gw }}</option>
      {% endfor %}
    </select>
  </div>
  <div class="field">
    <label>Dispositivo</label>
    <select name="device_mac">
      <option value="all" {% if selected_device=='all' %}selected{% endif %}>Todos</option>
      {% for dev in known_devices %}
        <option value="{{ dev.device_mac }}" {% if dev.device_mac==selected_device %}selected{% endif %}>
          {{ dev.device_name }} ({{ dev.device_mac }})
        </option>
      {% endfor %}
    </select>
  </div>
  <div class="field">
    <label>Desde</label>
    <input type="date" name="date_from" value="{{ date_from }}">
  </div>
  <div class="field">
    <label>Hasta</label>
    <input type="date" name="date_to" value="{{ date_to }}">
  </div>
  <div style="display:flex;gap:.5rem;align-items:flex-end">
    <button type="submit" class="btn btn-primary">&#128269; Filtrar</button>
    <a href="/" class="btn btn-outline">&#10006; Limpiar</a>
  </div>
</form>

<div class="stats">
  <div class="stat-card"><div class="stat-label">Total mostrados</div><div class="stat-value c-blue">{{ devices|length }}</div></div>
  <div class="stat-card"><div class="stat-label">En l&iacute;nea</div><div class="stat-value c-green">{{ online_count }}</div></div>
  <div class="stat-card"><div class="stat-label">Encendidos</div><div class="stat-value c-yellow">{{ power_on_count }}</div></div>
  <div class="stat-card"><div class="stat-label">Gateway activo</div><div class="stat-value c-muted">{{ selected_gateway }}</div></div>
  <div class="stat-card"><div class="stat-label">&Uacute;ltima actualizaci&oacute;n</div><div class="stat-value c-muted">{{ last_updated }}</div></div>
</div>

<p style="font-size:.82rem;color:var(--muted);margin-bottom:1rem">
  Vista actual obtenida desde la API de Govee.
  {% if selected_gateway != 'all' %} &mdash; Gateway: <strong style="color:var(--blue)">{{ selected_gateway }}</strong>{% endif %}
  {% if date_from %} &mdash; Desde <strong>{{ date_from }}</strong>{% endif %}
  {% if date_to %} hasta <strong>{{ date_to }}</strong>{% endif %}
</p>

<div class="actions">
  <a class="btn btn-primary" href="/download.csv?gateway={{ selected_gateway }}&date_from={{ date_from }}&date_to={{ date_to }}&device_mac={{ selected_device }}">&#11015;&#65039; Descargar CSV</a>
  <a class="btn btn-outline" href="/snapshot?gateway={{ selected_gateway }}">&#128247; Capturar ahora</a>
  <a class="btn btn-outline" href="/">&#128260; Actualizar</a>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Dispositivo</th><th>Modelo</th><th>MAC / ID</th><th>Gateway</th>
        <th>Fecha/Hora</th><th>Online</th><th>Power</th><th>Brightness</th>
        <th>Temperatura (&deg;C)</th><th>Humedad (%)</th>
      </tr>
    </thead>
    <tbody>
      {% for device in devices %}
      <tr>
        <td><strong>{{ device.name }}</strong></td>
        <td style="color:var(--muted)">{{ device.model }}</td>
        <td style="font-family:monospace;font-size:.76rem;color:var(--muted)">{{ device.device }}</td>
        <td><span class="pill" style="background:rgba(59,130,246,.15);color:var(--blue)">{{ device.gateway }}</span></td>
        <td style="font-size:.78rem;color:var(--muted)">{{ device.recorded_at }}</td>
        <td>{% if device.online %}<span class="pill p-online">&bull; Online</span>
            {% else %}<span class="pill p-offline">&circ; Offline</span>{% endif %}</td>
        <td>{% if device.power_state == 'on' %}<span class="pill p-on">&#9889; On</span>
            {% elif device.power_state == 'off' %}<span class="pill p-off">Off</span>
            {% else %}<span class="na">&mdash;</span>{% endif %}</td>
        <td>{% if device.brightness is not none %}<span class="v-blue">{{ device.brightness }}%</span>
            {% else %}<span class="na">&mdash;</span>{% endif %}</td>
        <td>{% if device.temperature is not none %}
              <span class="v-temp">{{ "%.2f"|format(device.temperature) }} &deg;C</span>
            {% else %}<span class="na">No disponible</span>{% endif %}</td>
        <td>{% if device.humidity is not none %}
              <span class="v-hum">{{ "%.2f"|format(device.humidity) }} %</span>
            {% else %}<span class="na">No disponible</span>{% endif %}</td>
      </tr>
      {% else %}
      <tr><td colspan="10" style="text-align:center;color:var(--muted);padding:2rem">Sin datos para los filtros seleccionados.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
<p class="footer-note">Govee Monitor &mdash; Datos via Govee API</p>
</body>
</html>
"""

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def dashboard():
    gateway    = request.args.get("gateway", "all")
    device_mac = request.args.get("device_mac", "all")
    date_from  = request.args.get("date_from", "")
    date_to    = request.args.get("date_to", "")
    error      = None
    devices    = []

    rows = query_history(gateway, date_from, date_to, device_mac)
    if not rows:
        try:
            live = fetch_and_store(gateway)
            rows = [{**d, "gateway": gateway, "device": d["device"],
                     "recorded_at": d["recorded_at"]} for d in live]
        except Exception as e:
            error = str(e)

    for r in rows:
        devices.append({
            "name":        r.get("device_name") or r.get("name", ""),
            "device":      r.get("device_mac")  or r.get("device", ""),
            "model":       r.get("model", ""),
            "gateway":     r.get("gateway", gateway),
            "online":      bool(r.get("online")),
            "power_state": r.get("power_state"),
            "brightness":  r.get("brightness"),
            "temperature": r.get("temperature"),
            "humidity":    r.get("humidity"),
            "recorded_at": r.get("recorded_at", ""),
        })

    return render_template_string(
        HTML_TEMPLATE,
        devices          = devices,
        online_count     = sum(1 for d in devices if d["online"]),
        power_on_count   = sum(1 for d in devices if d["power_state"] == "on"),
        last_updated     = datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        gateways         = get_known_gateways(),
        known_devices    = get_known_devices(gateway),
        selected_gateway = gateway,
        selected_device  = device_mac,
        date_from        = date_from,
        date_to          = date_to,
        error            = error,
    )

@app.route("/snapshot")
def snapshot():
    from flask import redirect
    gateway = request.args.get("gateway", "all")
    try:
        fetch_and_store(gateway)
    except Exception:
        pass
    return redirect(f"/?gateway={gateway}")

@app.route("/download.csv")
def download_csv():
    gateway    = request.args.get("gateway", "all")
    device_mac = request.args.get("device_mac", "all")
    date_from  = request.args.get("date_from", "")
    date_to    = request.args.get("date_to", "")
    rows       = query_history(gateway, date_from, date_to, device_mac)
    output     = io.StringIO()
    writer     = csv.writer(output)
    writer.writerow(["gateway","device_name","device_mac","model",
                     "recorded_at","online","power_state",
                     "brightness","temperature_c","humidity_pct"])
    for r in rows:
        writer.writerow([
            r.get("gateway",""), r.get("device_name",""), r.get("device_mac",""),
            r.get("model",""),   r.get("recorded_at",""),
            "online" if r.get("online") else "offline",
            r.get("power_state") or "", r.get("brightness") or "",
            f"{r['temperature']:.2f}" if r.get("temperature") is not None else "",
            f"{r['humidity']:.2f}"    if r.get("humidity")    is not None else "",
        ])
    csv_data = output.getvalue()
    output.close()
    fname = f"govee_{gateway}_{date_from or 'all'}_{date_to or 'all'}.csv"
    return Response(csv_data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})

@app.route("/api/devices")
def api_devices():
    from flask import jsonify
    gateway = request.args.get("gateway", "all")
    try:
        data = fetch_and_store(gateway)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"gateway": gateway, "count": len(data), "devices": data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
