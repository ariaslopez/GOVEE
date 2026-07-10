import os
import io
import csv
import json
import sqlite3
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, Response, render_template_string, request, g, redirect, jsonify
from dotenv import load_dotenv

load_dotenv()

BASE_URL_V2 = "https://openapi.api.govee.com/router/api/v1"
DB_PATH     = os.getenv("DB_PATH", "govee_history.db")
GROUPS_FILE = os.getenv("GROUPS_FILE", "groups.json")

# Zona horaria Colombia UTC-5 (no tiene horario de verano)
COL_TZ = timezone(timedelta(hours=-5))

def now_col():
    return datetime.now(COL_TZ).strftime("%Y-%m-%d %H:%M:%S")

def now_col_display():
    return datetime.now(COL_TZ).strftime("%d/%m/%Y %H:%M:%S")

app = Flask(__name__)

# ─────────────────────────────────────────────
# GROUPS (agrupaciones con nombre personalizado)
# groups.json: {"Bodega Norte": ["D0:C9:07:...", "D0:C9:07:..."], "Oficina": [...]}
# ─────────────────────────────────────────────
def load_groups() -> dict:
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_groups(groups: dict):
    with open(GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

def get_device_group(device_id: str) -> str:
    """Retorna el nombre del grupo al que pertenece device_id, o '' si ninguno."""
    for gname, members in load_groups().items():
        if device_id in members:
            return gname
    return ""

# ─────────────────────────────────────────────
# DATABASE
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
            device_group TEXT   NOT NULL DEFAULT '',
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
    # migracion: agregar columna si no existe (BD previa)
    try:
        db.execute("ALTER TABLE readings ADD COLUMN device_group TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    db.commit()
    db.close()

init_db()

# ─────────────────────────────────────────────
# GOVEE API v2
# ─────────────────────────────────────────────
def _headers():
    key = os.getenv("GOVEE_API_KEY")
    if not key:
        raise RuntimeError("Missing GOVEE_API_KEY. Verifica tu archivo .env")
    return {"Govee-API-Key": key, "Content-Type": "application/json"}

def v2_get_devices():
    resp = requests.get(f"{BASE_URL_V2}/user/devices", headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", [])

def v2_get_state(sku, device_id):
    body = {"requestId": "govee-monitor", "payload": {"sku": sku, "device": device_id}}
    resp = requests.post(f"{BASE_URL_V2}/device/state", headers=_headers(), json=body, timeout=15)
    resp.raise_for_status()
    return resp.json().get("payload", {})

def extract_v2_state(capabilities):
    online = False; power_state = brightness = temperature = humidity = None
    for cap in capabilities:
        t    = cap.get("type", "")
        inst = cap.get("instance", "")
        val  = cap.get("state", {}).get("value")
        if t == "devices.capabilities.online":
            online = bool(val)
        elif t == "devices.capabilities.on_off" and inst == "powerSwitch":
            power_state = "on" if val == 1 else "off"
        elif t == "devices.capabilities.range" and inst == "brightness":
            brightness = val
        elif t == "devices.capabilities.property":
            if inst == "sensorTemperature" and val is not None:
                v = float(val); temperature = v / 100 if v > 1000 else v
            elif inst == "sensorHumidity" and val is not None:
                v = float(val); humidity = v / 100 if v > 1000 else v
        elif inst == "temperature" and val is not None and temperature is None:
            v = float(val); temperature = v / 100 if v > 1000 else v
        elif inst == "humidity" and val is not None and humidity is None:
            v = float(val); humidity = v / 100 if v > 1000 else v
    return online, power_state, brightness, temperature, humidity

def _parse_gateway_map():
    raw = os.getenv("GATEWAY_MAP", "")
    result = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":", 1)
        if len(parts) == 2:
            result[parts[0].strip()] = parts[1].strip()
    return result

def fetch_and_store(gateway: str):
    raw_devices = v2_get_devices()
    now         = now_col()
    db          = get_db()
    results     = []
    gw_map      = _parse_gateway_map()

    for d in raw_devices:
        device_id = d.get("device", "")
        sku       = d.get("sku", "")
        name      = d.get("deviceName", "Unnamed")
        grp       = get_device_group(device_id)

        if gw_map and gateway != "all":
            prefix = gw_map.get(gateway, "")
            if prefix and not device_id.startswith(prefix):
                continue

        try:
            state_payload = v2_get_state(sku, device_id)
            capabilities  = state_payload.get("capabilities", [])
            online, power_state, brightness, temperature, humidity = extract_v2_state(capabilities)
        except Exception:
            online = False; power_state = brightness = temperature = humidity = None

        db.execute("""
            INSERT INTO readings
              (gateway, device_group, device_mac, device_name, model, online,
               power_state, brightness, temperature, humidity, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (gateway, grp, device_id, name, sku, int(online),
               power_state, brightness, temperature, humidity, now))

        results.append({
            "name": name, "device": device_id, "model": sku, "group": grp,
            "online": online, "power_state": power_state,
            "brightness": brightness, "temperature": temperature,
            "humidity": humidity, "recorded_at": now,
        })

    db.commit()
    return results

def query_history(gateway, date_from, date_to, device_mac, group):
    db     = get_db()
    sql    = "SELECT * FROM readings WHERE 1=1"
    params = []
    if gateway and gateway != "all":
        sql += " AND gateway = ?"; params.append(gateway)
    if group and group != "all":
        sql += " AND device_group = ?"; params.append(group)
    if date_from:
        sql += " AND recorded_at >= ?"; params.append(date_from + " 00:00:00")
    if date_to:
        sql += " AND recorded_at <= ?"; params.append(date_to + " 23:59:59")
    if device_mac and device_mac != "all":
        sql += " AND device_mac = ?"; params.append(device_mac)
    sql += " ORDER BY recorded_at DESC LIMIT 500"
    return [dict(r) for r in db.execute(sql, params).fetchall()]

def get_known_gateways():
    rows = get_db().execute("SELECT DISTINCT gateway FROM readings ORDER BY gateway").fetchall()
    return [r["gateway"] for r in rows] or []

def get_known_devices(gateway, group):
    db = get_db(); sql = "SELECT DISTINCT device_mac, device_name FROM readings WHERE 1=1"
    params = []
    if gateway and gateway != "all":
        sql += " AND gateway=?"; params.append(gateway)
    if group and group != "all":
        sql += " AND device_group=?"; params.append(group)
    sql += " ORDER BY device_name"
    return [dict(r) for r in db.execute(sql, params).fetchall()]

# ─────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────
HTML_TEMPLATE = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Govee Monitor</title>
  <style>
    :root{
      --bg:#0f172a;--surface:#1e293b;--border:#334155;
      --blue:#3b82f6;--cyan:#06b6d4;--orange:#f97316;
      --green:#22c55e;--red:#ef4444;--yellow:#eab308;
      --purple:#a855f7;--text:#f1f5f9;--muted:#94a3b8;
    }
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:2rem 1.5rem}
    header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem;
           margin-bottom:1.75rem;padding-bottom:1.25rem;border-bottom:1px solid var(--border)}
    header h1{font-size:1.4rem;font-weight:700;display:flex;align-items:center;gap:.5rem}
    .badge{font-size:.65rem;background:var(--blue);color:#fff;padding:.2rem .55rem;border-radius:999px;font-weight:700}
    .live-dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite;display:inline-block}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
    /* FILTER BAR */
    .filter-bar{background:var(--surface);border:1px solid var(--border);border-radius:14px;
      padding:1.25rem 1.5rem;margin-bottom:1.5rem;display:flex;flex-wrap:wrap;gap:1rem;align-items:flex-end}
    .field{display:flex;flex-direction:column;gap:.3rem;min-width:150px;flex:1}
    .field label{font-size:.68rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
    .field select,.field input{background:var(--bg);color:var(--text);border:1px solid var(--border);
      border-radius:8px;padding:.42rem .7rem;font-size:.83rem;outline:none}
    .field select:focus,.field input:focus{border-color:var(--blue)}
    /* BUTTONS */
    .btn{display:inline-flex;align-items:center;gap:.35rem;padding:.48rem 1rem;
         border-radius:8px;font-size:.83rem;font-weight:600;text-decoration:none;
         border:none;cursor:pointer;transition:opacity .2s}
    .btn:hover{opacity:.82}
    .btn-primary{background:var(--blue);color:#fff}
    .btn-success{background:var(--green);color:#000}
    .btn-danger{background:var(--red);color:#fff}
    .btn-purple{background:var(--purple);color:#fff}
    .btn-outline{background:transparent;color:var(--text);border:1px solid var(--border)}
    /* STATS */
    .stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:1rem;margin-bottom:1.75rem}
    .stat-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1rem}
    .stat-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:.3rem}
    .stat-value{font-size:1.85rem;font-weight:800;line-height:1}
    .c-blue{color:var(--blue)}.c-green{color:var(--green)}.c-yellow{color:var(--yellow)}.c-purple{color:var(--purple)}
    .c-muted{color:var(--muted);font-size:.9rem;font-weight:500}
    /* GROUPS PANEL */
    .groups-section{background:var(--surface);border:1px solid var(--border);border-radius:14px;
      padding:1.25rem 1.5rem;margin-bottom:1.5rem}
    .groups-section h2{font-size:.85rem;font-weight:700;color:var(--muted);text-transform:uppercase;
      letter-spacing:.07em;margin-bottom:1rem}
    .group-list{display:flex;flex-wrap:wrap;gap:.6rem;margin-bottom:1rem}
    .group-tag{display:inline-flex;align-items:center;gap:.4rem;background:rgba(168,85,247,.15);
      color:var(--purple);border:1px solid rgba(168,85,247,.3);border-radius:999px;
      padding:.3rem .8rem;font-size:.78rem;font-weight:600}
    .group-tag .del{cursor:pointer;opacity:.6;font-size:.9rem}
    .group-tag .del:hover{opacity:1}
    .group-form{display:flex;gap:.6rem;flex-wrap:wrap;align-items:flex-end}
    .group-form input,.group-form select{background:var(--bg);color:var(--text);
      border:1px solid var(--border);border-radius:8px;padding:.42rem .7rem;
      font-size:.83rem;outline:none;min-width:160px}
    .group-form input:focus,.group-form select:focus{border-color:var(--purple)}
    /* TABLE */
    .table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:14px;margin-top:.5rem}
    table{border-collapse:collapse;width:100%;min-width:950px}
    thead{background:var(--surface)}
    th,td{padding:.68rem 1rem;text-align:left;font-size:.81rem;border-bottom:1px solid var(--border)}
    th{color:var(--muted);font-size:.67rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em}
    tr:last-child td{border-bottom:none}
    tr:hover td{background:rgba(255,255,255,.025)}
    .pill{display:inline-block;padding:.14rem .48rem;border-radius:999px;font-size:.69rem;font-weight:700}
    .p-online{background:rgba(34,197,94,.15);color:var(--green)}
    .p-offline{background:rgba(239,68,68,.15);color:var(--red)}
    .p-on{background:rgba(234,179,8,.15);color:var(--yellow)}
    .p-off{background:rgba(148,163,184,.1);color:var(--muted)}
    .p-group{background:rgba(168,85,247,.15);color:var(--purple)}
    .p-gw{background:rgba(59,130,246,.15);color:var(--blue)}
    .v-temp{color:var(--orange);font-weight:700}
    .v-hum{color:var(--cyan);font-weight:700}
    .v-blue{color:var(--blue);font-weight:600}
    .na{color:var(--muted);font-style:italic}
    .actions{display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1rem}
    .footer-note{margin-top:1.5rem;font-size:.7rem;color:var(--muted);text-align:center}
    .error-banner{background:rgba(239,68,68,.15);border:1px solid var(--red);color:var(--red);
      border-radius:10px;padding:1rem 1.25rem;margin-bottom:1.5rem;font-size:.84rem}
    .info-bar{font-size:.81rem;color:var(--muted);margin-bottom:1rem}
  </style>
</head>
<body>
<header>
  <h1>&#127774; Govee Monitor <span class="badge">LIVE</span></h1>
  <span style="font-size:.78rem;color:var(--muted);display:flex;align-items:center;gap:.4rem">
    <span class="live-dot"></span> Hora Colombia &bull; {{ last_updated }}
  </span>
</header>

{% if error %}
<div class="error-banner">&#9888; <strong>Error Govee API v2:</strong> {{ error }}
  <br><small>Verifica <code>GOVEE_API_KEY</code> en <code>.env</code></small></div>
{% endif %}

<!-- AGRUPACIONES -->
<div class="groups-section">
  <h2>&#128101; Agrupaciones de dispositivos</h2>

  {% if groups %}
  <div class="group-list">
    {% for gname, members in groups.items() %}
    <span class="group-tag">
      &#128193; {{ gname }} <small style="opacity:.7">({{ members|length }})</small>
      <a class="del" href="/groups/delete?name={{ gname }}" title="Eliminar grupo">&#10005;</a>
    </span>
    {% endfor %}
  </div>
  {% else %}
  <p style="font-size:.8rem;color:var(--muted);margin-bottom:.75rem">Sin agrupaciones creadas aun.</p>
  {% endif %}

  <!-- Formulario crear/asignar grupo -->
  <form method="POST" action="/groups/save" class="group-form">
    <div>
      <label style="font-size:.68rem;text-transform:uppercase;color:var(--muted);display:block;margin-bottom:.25rem">Nombre del grupo</label>
      <input type="text" name="group_name" placeholder="Ej: Bodega Norte" required>
    </div>
    <div>
      <label style="font-size:.68rem;text-transform:uppercase;color:var(--muted);display:block;margin-bottom:.25rem">Dispositivos (selecciona varios)</label>
      <select name="device_ids" multiple size="3" style="min-width:260px">
        {% for dev in all_devices %}
        <option value="{{ dev.device_mac }}"
          {% if dev.device_mac in (groups.get(group_name_prefill, [])) %}selected{% endif %}>
          {{ dev.device_name }} — {{ dev.device_mac }}
        </option>
        {% endfor %}
      </select>
    </div>
    <button type="submit" class="btn btn-purple">&#10010; Guardar grupo</button>
  </form>
</div>

<!-- FILTROS -->
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
    <label>Agrupacion</label>
    <select name="group">
      <option value="all" {% if selected_group=='all' %}selected{% endif %}>Todas</option>
      {% for gname in groups.keys() %}
        <option value="{{ gname }}" {% if gname==selected_group %}selected{% endif %}>{{ gname }}</option>
      {% endfor %}
    </select>
  </div>
  <div class="field">
    <label>Dispositivo</label>
    <select name="device_mac">
      <option value="all" {% if selected_device=='all' %}selected{% endif %}>Todos</option>
      {% for dev in known_devices %}
        <option value="{{ dev.device_mac }}" {% if dev.device_mac==selected_device %}selected{% endif %}>
          {{ dev.device_name }} ({{ dev.device_mac[:14] }}...)
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

<!-- STATS -->
<div class="stats">
  <div class="stat-card"><div class="stat-label">Dispositivos</div><div class="stat-value c-blue">{{ devices|length }}</div></div>
  <div class="stat-card"><div class="stat-label">En l&iacute;nea</div><div class="stat-value c-green">{{ online_count }}</div></div>
  <div class="stat-card"><div class="stat-label">Encendidos</div><div class="stat-value c-yellow">{{ power_on_count }}</div></div>
  <div class="stat-card"><div class="stat-label">Agrupaciones</div><div class="stat-value c-purple">{{ groups|length }}</div></div>
  <div class="stat-card"><div class="stat-label">Gateway</div><div class="stat-value c-muted">{{ selected_gateway }}</div></div>
</div>

<p class="info-bar">
  Datos en tiempo real &bull; API v2 &bull; Hora Colombia (UTC-5)
  {% if selected_group != 'all' %} &mdash; Grupo: <strong style="color:var(--purple)">{{ selected_group }}</strong>{% endif %}
  {% if selected_gateway != 'all' %} &mdash; GW: <strong style="color:var(--blue)">{{ selected_gateway }}</strong>{% endif %}
  {% if date_from %} &mdash; Desde <strong>{{ date_from }}</strong>{% endif %}
  {% if date_to %} hasta <strong>{{ date_to }}</strong>{% endif %}
</p>

<div class="actions">
  <a class="btn btn-primary" href="/download.csv?gateway={{ selected_gateway }}&group={{ selected_group }}&date_from={{ date_from }}&date_to={{ date_to }}&device_mac={{ selected_device }}">&#11015;&#65039; Descargar CSV</a>
  <a class="btn btn-outline" href="/snapshot?gateway={{ selected_gateway }}">&#128247; Capturar ahora</a>
  <a class="btn btn-outline" href="/">&#128260; Actualizar</a>
</div>

<!-- TABLA -->
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Dispositivo</th><th>Modelo</th><th>Device ID</th>
        <th>Gateway</th><th>Agrupacion</th><th>Fecha/Hora (CO)</th>
        <th>Online</th><th>Power</th><th>Brightness</th>
        <th>Temp &deg;C</th><th>Humedad %</th>
      </tr>
    </thead>
    <tbody>
      {% for device in devices %}
      <tr>
        <td><strong>{{ device.name }}</strong></td>
        <td style="color:var(--muted)">{{ device.model }}</td>
        <td style="font-family:monospace;font-size:.74rem;color:var(--muted)">{{ device.device }}</td>
        <td><span class="pill p-gw">{{ device.gateway }}</span></td>
        <td>
          {% if device.group %}
            <span class="pill p-group">&#128193; {{ device.group }}</span>
          {% else %}
            <span class="na">&mdash;</span>
          {% endif %}
        </td>
        <td style="font-size:.76rem;color:var(--muted)">{{ device.recorded_at }}</td>
        <td>{% if device.online %}<span class="pill p-online">&bull; Online</span>
            {% else %}<span class="pill p-offline">&circ; Offline</span>{% endif %}</td>
        <td>{% if device.power_state == 'on' %}<span class="pill p-on">&#9889; On</span>
            {% elif device.power_state == 'off' %}<span class="pill p-off">Off</span>
            {% else %}<span class="na">&mdash;</span>{% endif %}</td>
        <td>{% if device.brightness is not none %}<span class="v-blue">{{ device.brightness }}%</span>
            {% else %}<span class="na">&mdash;</span>{% endif %}</td>
        <td>{% if device.temperature is not none %}
              <span class="v-temp">{{ "%.2f"|format(device.temperature) }} &deg;C</span>
            {% else %}<span class="na">No disp.</span>{% endif %}</td>
        <td>{% if device.humidity is not none %}
              <span class="v-hum">{{ "%.2f"|format(device.humidity) }} %</span>
            {% else %}<span class="na">No disp.</span>{% endif %}</td>
      </tr>
      {% else %}
      <tr><td colspan="11" style="text-align:center;color:var(--muted);padding:2rem">Sin datos para los filtros seleccionados.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
<p class="footer-note">Govee Monitor &mdash; API v2 &bull; Hora Colombia UTC-5</p>
</body>
</html>
"""

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def dashboard():
    gateway    = request.args.get("gateway", "all")
    group      = request.args.get("group", "all")
    device_mac = request.args.get("device_mac", "all")
    date_from  = request.args.get("date_from", "")
    date_to    = request.args.get("date_to", "")
    error      = None; devices = []

    rows = query_history(gateway, date_from, date_to, device_mac, group)
    if not rows:
        try:
            live = fetch_and_store(gateway)
            rows = [{**d, "gateway": gateway, "device_group": d.get("group","")} for d in live]
        except Exception as e:
            error = str(e)

    for r in rows:
        devices.append({
            "name":        r.get("device_name") or r.get("name", ""),
            "device":      r.get("device_mac")  or r.get("device", ""),
            "model":       r.get("model", ""),
            "gateway":     r.get("gateway", gateway),
            "group":       r.get("device_group") or r.get("group", ""),
            "online":      bool(r.get("online")),
            "power_state": r.get("power_state"),
            "brightness":  r.get("brightness"),
            "temperature": r.get("temperature"),
            "humidity":    r.get("humidity"),
            "recorded_at": r.get("recorded_at", ""),
        })

    groups      = load_groups()
    all_devices = get_known_devices("all", "all")

    return render_template_string(
        HTML_TEMPLATE,
        devices          = devices,
        online_count     = sum(1 for d in devices if d["online"]),
        power_on_count   = sum(1 for d in devices if d["power_state"] == "on"),
        last_updated     = now_col_display(),
        gateways         = get_known_gateways(),
        known_devices    = get_known_devices(gateway, group),
        all_devices      = all_devices,
        groups           = groups,
        group_name_prefill = "",
        selected_gateway = gateway,
        selected_group   = group,
        selected_device  = device_mac,
        date_from        = date_from,
        date_to          = date_to,
        error            = error,
    )

# ── Guardar / actualizar grupo ─────────────────────
@app.route("/groups/save", methods=["POST"])
def groups_save():
    gname      = request.form.get("group_name", "").strip()
    device_ids = request.form.getlist("device_ids")
    if gname:
        groups = load_groups()
        groups[gname] = device_ids
        save_groups(groups)
    return redirect("/")

# ── Eliminar grupo ─────────────────────────────────
@app.route("/groups/delete")
def groups_delete():
    gname  = request.args.get("name", "")
    groups = load_groups()
    groups.pop(gname, None)
    save_groups(groups)
    return redirect("/")

@app.route("/snapshot")
def snapshot():
    gateway = request.args.get("gateway", "all")
    try:
        fetch_and_store(gateway)
    except Exception:
        pass
    return redirect(f"/?gateway={gateway}")

@app.route("/download.csv")
def download_csv():
    gateway    = request.args.get("gateway", "all")
    group      = request.args.get("group", "all")
    device_mac = request.args.get("device_mac", "all")
    date_from  = request.args.get("date_from", "")
    date_to    = request.args.get("date_to", "")
    rows       = query_history(gateway, date_from, date_to, device_mac, group)
    output     = io.StringIO()
    writer     = csv.writer(output)
    writer.writerow(["gateway","agrupacion","device_name","device_mac","model",
                     "recorded_at_colombia","online","power_state",
                     "brightness","temperature_c","humidity_pct"])
    for r in rows:
        writer.writerow([
            r.get("gateway",""), r.get("device_group",""),
            r.get("device_name",""), r.get("device_mac",""), r.get("model",""),
            r.get("recorded_at",""),
            "online" if r.get("online") else "offline",
            r.get("power_state") or "", r.get("brightness") or "",
            f"{r['temperature']:.2f}" if r.get("temperature") is not None else "",
            f"{r['humidity']:.2f}"    if r.get("humidity")    is not None else "",
        ])
    csv_data = output.getvalue(); output.close()
    fname = f"govee_{gateway}_{group}_{date_from or 'all'}_{date_to or 'all'}.csv"
    return Response(csv_data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})

@app.route("/api/devices")
def api_devices():
    gateway = request.args.get("gateway", "all")
    try:
        data = fetch_and_store(gateway)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"gateway": gateway, "count": len(data), "devices": data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
