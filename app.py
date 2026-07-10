import os
import io
import csv
import requests
from datetime import datetime
from flask import Flask, Response, render_template_string

GOVEE_API_KEY = os.getenv("GOVEE_API_KEY")
BASE_URL = "https://developer-api.govee.com/v1"

app = Flask(__name__)

def govee_get(path, params=None):
    if not GOVEE_API_KEY:
        raise RuntimeError("Missing GOVEE_API_KEY environment variable")
    headers = {"Govee-API-Key": GOVEE_API_KEY}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params or {})
    resp.raise_for_status()
    return resp.json()

def extract_sensor_data(raw_state):
    temperature = None
    humidity = None
    try:
        props = raw_state.get("data", {}).get("properties", [])
        for prop in props:
            if "temperature" in prop:
                val = prop["temperature"]
                temperature = val / 100 if val > 1000 else val
            if "humidity" in prop:
                val = prop["humidity"]
                humidity = val / 100 if val > 1000 else val
    except Exception:
        pass
    if temperature is None:
        try:
            val = raw_state.get("temperature") or raw_state.get("data", {}).get("temperature")
            if val is not None:
                temperature = val / 100 if val > 1000 else val
        except Exception:
            pass
    if humidity is None:
        try:
            val = raw_state.get("humidity") or raw_state.get("data", {}).get("humidity")
            if val is not None:
                humidity = val / 100 if val > 1000 else val
        except Exception:
            pass
    return temperature, humidity

def get_devices_and_states():
    devices_resp = govee_get("/devices")
    devices = devices_resp.get("data", {}).get("devices", [])
    results = []
    for d in devices:
        mac   = d.get("device")
        model = d.get("model")
        name  = d.get("deviceName", "Unnamed device")
        state_resp = govee_get("/devices", params={"device": mac, "model": model})
        temperature, humidity = extract_sensor_data(state_resp)
        props = state_resp.get("data", {}).get("properties", [])
        online      = state_resp.get("data", {}).get("online", False)
        power_state = None
        brightness  = None
        for prop in props:
            if "powerSwitch" in prop:
                power_state = "on" if prop["powerSwitch"] == 1 else "off"
            if "brightness" in prop:
                brightness = prop["brightness"]
        results.append({
            "name":        name,
            "device":      mac,
            "model":       model,
            "online":      online,
            "power_state": power_state,
            "brightness":  brightness,
            "temperature": temperature,
            "humidity":    humidity,
        })
    return results

HTML_TEMPLATE = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Govee — Monitor de Dispositivos</title>
  <meta http-equiv="refresh" content="30">
  <style>
    :root {
      --bg:      #0f172a;
      --surface: #1e293b;
      --border:  #334155;
      --blue:    #3b82f6;
      --cyan:    #06b6d4;
      --orange:  #f97316;
      --green:   #22c55e;
      --red:     #ef4444;
      --yellow:  #eab308;
      --text:    #f1f5f9;
      --muted:   #94a3b8;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem 1.5rem;
    }

    /* ---- HEADER ---- */
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 2rem;
      padding-bottom: 1.25rem;
      border-bottom: 1px solid var(--border);
    }
    header h1 { font-size: 1.5rem; font-weight: 700; display: flex; align-items: center; gap: 0.5rem; }
    .badge {
      font-size: 0.65rem; background: var(--blue); color: white;
      padding: 0.2rem 0.55rem; border-radius: 999px; font-weight: 700; letter-spacing: 0.06em;
    }
    .live-indicator { display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--muted); }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

    /* ---- STATS SUMMARY ---- */
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    .stat-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.2rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .stat-label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); }
    .stat-value { font-size: 2rem; font-weight: 800; line-height: 1; }
    .stat-value.blue   { color: var(--blue); }
    .stat-value.green  { color: var(--green); }
    .stat-value.yellow { color: var(--yellow); }
    .stat-value.muted  { color: var(--muted); font-size: 1rem; font-weight: 500; margin-top: 0.2rem; }

    /* ---- ACTIONS ---- */
    .actions { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
    .btn {
      display: inline-flex; align-items: center; gap: 0.35rem;
      padding: 0.5rem 1.1rem; border-radius: 8px;
      font-size: 0.85rem; font-weight: 600; text-decoration: none;
      transition: opacity 0.2s;
    }
    .btn:hover { opacity: 0.82; }
    .btn-primary { background: var(--blue); color: white; }
    .btn-outline { background: transparent; color: var(--text); border: 1px solid var(--border); }

    /* ---- TABLE ---- */
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 14px;
    }
    table { border-collapse: collapse; width: 100%; min-width: 820px; }
    thead { background: var(--surface); }
    th, td { padding: 0.75rem 1rem; text-align: left; font-size: 0.85rem; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.025); }

    /* Status pills */
    .pill {
      display: inline-block; padding: 0.18rem 0.55rem;
      border-radius: 999px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.04em;
    }
    .pill-online  { background: rgba(34,197,94,0.15);  color: var(--green); }
    .pill-offline { background: rgba(239,68,68,0.15);  color: var(--red); }
    .pill-on      { background: rgba(234,179,8,0.15);  color: var(--yellow); }
    .pill-off     { background: rgba(148,163,184,0.1); color: var(--muted); }

    .val-temp { color: var(--orange); font-weight: 700; }
    .val-hum  { color: var(--cyan);   font-weight: 700; }
    .val-bright { color: var(--blue); font-weight: 600; }
    .na { color: var(--muted); font-style: italic; }

    /* ---- FOOTER ---- */
    .footer-note {
      margin-top: 1.5rem;
      font-size: 0.72rem;
      color: var(--muted);
      text-align: center;
    }
  </style>
</head>
<body>

  <header>
    <h1>&#127774; Govee Monitor <span class="badge">LIVE</span></h1>
    <div class="live-indicator">
      <div class="dot"></div>
      Auto-actualización cada 30 s
    </div>
  </header>

  <!-- STATS SUMMARY -->
  <div class="stats">
    <div class="stat-card">
      <span class="stat-label">Total dispositivos</span>
      <span class="stat-value blue">{{ devices|length }}</span>
    </div>
    <div class="stat-card">
      <span class="stat-label">En línea</span>
      <span class="stat-value green">{{ online_count }}</span>
    </div>
    <div class="stat-card">
      <span class="stat-label">Encendidos</span>
      <span class="stat-value yellow">{{ power_on_count }}</span>
    </div>
    <div class="stat-card">
      <span class="stat-label">Última actualización</span>
      <span class="stat-value muted">{{ last_updated }}</span>
    </div>
  </div>

  <p style="font-size:0.82rem; color:var(--muted); margin-bottom:1.25rem;">
    Vista actual obtenida desde la API de Govee.
  </p>

  <!-- ACTIONS -->
  <div class="actions">
    <a class="btn btn-primary" href="/download.csv">⬇️ Descargar CSV</a>
    <a class="btn btn-outline" href="/">🔄 Actualizar ahora</a>
  </div>

  <!-- TABLE -->
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Dispositivo</th>
          <th>Modelo</th>
          <th>MAC / ID</th>
          <th>Online</th>
          <th>Power</th>
          <th>Brightness</th>
          <th>Temperatura (°C)</th>
          <th>Humedad (%)</th>
        </tr>
      </thead>
      <tbody>
        {% for device in devices %}
        <tr>
          <td>{{ device.name }}</td>
          <td style="color:var(--muted)">{{ device.model }}</td>
          <td style="font-family:monospace; font-size:0.78rem; color:var(--muted)">{{ device.device }}</td>
          <td>
            {% if device.online %}
              <span class="pill pill-online">● Online</span>
            {% else %}
              <span class="pill pill-offline">○ Offline</span>
            {% endif %}
          </td>
          <td>
            {% if device.power_state == 'on' %}
              <span class="pill pill-on">⚡ On</span>
            {% elif device.power_state == 'off' %}
              <span class="pill pill-off">Off</span>
            {% else %}
              <span class="na">—</span>
            {% endif %}
          </td>
          <td>
            {% if device.brightness is not none %}
              <span class="val-bright">{{ device.brightness }}%</span>
            {% else %}
              <span class="na">—</span>
            {% endif %}
          </td>
          <td>
            {% if device.temperature is not none %}
              <span class="val-temp">{{ "%.2f"|format(device.temperature) }} °C</span>
            {% else %}
              <span class="na">No disponible</span>
            {% endif %}
          </td>
          <td>
            {% if device.humidity is not none %}
              <span class="val-hum">{{ "%.2f"|format(device.humidity) }} %</span>
            {% else %}
              <span class="na">No disponible</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <p class="footer-note">Govee Monitor — Datos en tiempo real via Govee API</p>

</body>
</html>
"""

@app.route("/")
def dashboard():
    devices     = get_devices_and_states()
    online_count   = sum(1 for d in devices if d["online"])
    power_on_count = sum(1 for d in devices if d["power_state"] == "on")
    last_updated   = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return render_template_string(
        HTML_TEMPLATE,
        devices=devices,
        online_count=online_count,
        power_on_count=power_on_count,
        last_updated=last_updated,
    )

@app.route("/download.csv")
def download_csv():
    devices = get_devices_and_states()
    output  = io.StringIO()
    writer  = csv.writer(output)
    writer.writerow(["name", "device", "model", "online", "power_state", "brightness", "temperature_c", "humidity_pct"])
    for d in devices:
        writer.writerow([
            d["name"], d["device"], d["model"],
            "online" if d["online"] else "offline",
            d["power_state"] or "",
            d["brightness"]  or "",
            f"{d['temperature']:.2f}" if d["temperature"] is not None else "",
            f"{d['humidity']:.2f}"    if d["humidity"]    is not None else "",
        ])
    csv_data = output.getvalue()
    output.close()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=govee_report.csv"}
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
