import os
import io
import csv
import requests
from flask import Flask, Response, render_template_string

# Configuración
GOVEE_API_KEY = os.getenv("GOVEE_API_KEY")
BASE_URL = "https://developer-api.govee.com/v1"

app = Flask(__name__)

def govee_get(path, params=None):
    """Call Govee API with the required Govee-API-Key header."""
    if not GOVEE_API_KEY:
        raise RuntimeError("Missing GOVEE_API_KEY environment variable")
    headers = {"Govee-API-Key": GOVEE_API_KEY}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params or {})
    resp.raise_for_status()
    return resp.json()

def extract_sensor_data(raw_state):
    """
    Extracts temperature and humidity from the raw state JSON.
    Handles common Govee API response structures.
    """
    temperature = None
    humidity = None

    # Try nested data.properties structure
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

    # Try flat structure
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
    """
    Returns a list of devices with their current sensor readings.
    """
    devices_resp = govee_get("/devices")
    devices = devices_resp.get("data", {}).get("devices", [])

    results = []
    for d in devices:
        mac = d.get("device")
        model = d.get("model")
        name = d.get("deviceName", "Unnamed device")

        state_resp = govee_get("/devices", params={"device": mac, "model": model})
        temperature, humidity = extract_sensor_data(state_resp)

        results.append({
            "name": name,
            "device": mac,
            "model": model,
            "temperature": temperature,
            "humidity": humidity,
            "raw_state": state_resp,
        })
    return results

HTML_TEMPLATE = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Govee — Monitor de Sensores</title>
  <meta http-equiv="refresh" content="30">
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --accent-blue: #3b82f6;
      --accent-cyan: #06b6d4;
      --accent-orange: #f97316;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --temp-color: #f97316;
      --hum-color: #06b6d4;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem 1.5rem;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
    }
    header h1 {
      font-size: 1.6rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .badge {
      font-size: 0.7rem;
      background: var(--accent-blue);
      color: white;
      padding: 0.2rem 0.6rem;
      border-radius: 999px;
      font-weight: 600;
      letter-spacing: 0.05em;
    }
    .auto-refresh {
      font-size: 0.8rem;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #22c55e;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.3; }
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1.25rem;
      margin-bottom: 2rem;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.5rem;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .card:hover {
      transform: translateY(-3px);
      box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 1.25rem;
    }
    .device-name {
      font-size: 1rem;
      font-weight: 600;
      line-height: 1.3;
    }
    .device-mac {
      font-size: 0.72rem;
      color: var(--muted);
      font-family: monospace;
      margin-top: 0.2rem;
    }
    .device-model {
      font-size: 0.7rem;
      color: var(--muted);
      background: var(--bg);
      border: 1px solid var(--border);
      padding: 0.2rem 0.5rem;
      border-radius: 6px;
      white-space: nowrap;
    }
    .sensors {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
    }
    .sensor-block {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem 0.75rem;
      text-align: center;
    }
    .sensor-icon {
      font-size: 1.6rem;
      margin-bottom: 0.4rem;
    }
    .sensor-label {
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 0.3rem;
    }
    .sensor-value {
      font-size: 1.8rem;
      font-weight: 800;
      line-height: 1;
    }
    .sensor-unit {
      font-size: 0.9rem;
      font-weight: 400;
      color: var(--muted);
    }
    .temp-value { color: var(--temp-color); }
    .hum-value  { color: var(--hum-color); }
    .no-data {
      font-size: 1.1rem;
      color: var(--muted);
    }
    .comfort-bar-wrap {
      margin-top: 1rem;
    }
    .comfort-label {
      font-size: 0.7rem;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      margin-bottom: 0.3rem;
    }
    .comfort-bar {
      height: 5px;
      background: var(--border);
      border-radius: 999px;
      overflow: hidden;
    }
    .comfort-fill {
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent-cyan), var(--temp-color));
      transition: width 0.5s;
    }
    .actions {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.55rem 1.2rem;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      text-decoration: none;
      transition: opacity 0.2s;
    }
    .btn:hover { opacity: 0.85; }
    .btn-primary { background: var(--accent-blue); color: white; }
    .btn-outline {
      background: transparent;
      color: var(--text);
      border: 1px solid var(--border);
    }
    .table-wrap {
      overflow-x: auto;
      margin-top: 2rem;
      border: 1px solid var(--border);
      border-radius: 12px;
    }
    table { border-collapse: collapse; width: 100%; }
    thead { background: var(--card); }
    th, td {
      padding: 0.75rem 1rem;
      text-align: left;
      font-size: 0.85rem;
      border-bottom: 1px solid var(--border);
    }
    th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 0.72rem; letter-spacing: 0.06em; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .val-temp { color: var(--temp-color); font-weight: 700; }
    .val-hum  { color: var(--hum-color);  font-weight: 700; }
    .na { color: var(--muted); font-style: italic; }
    footer { margin-top: 2.5rem; text-align: center; font-size: 0.75rem; color: var(--muted); }
  </style>
</head>
<body>
  <header>
    <h1>🌡️ Govee Sensor Monitor <span class="badge">LIVE</span></h1>
    <div class="auto-refresh">
      <div class="dot"></div>
      Auto-actualización cada 30 s
    </div>
  </header>

  <!-- TARJETAS -->
  <div class="grid">
    {% for d in devices %}
    <div class="card">
      <div class="card-header">
        <div>
          <div class="device-name">{{ d.name }}</div>
          <div class="device-mac">{{ d.device }}</div>
        </div>
        <span class="device-model">{{ d.model }}</span>
      </div>

      <div class="sensors">
        <!-- Temperatura -->
        <div class="sensor-block">
          <div class="sensor-icon">🌡️</div>
          <div class="sensor-label">Temperatura</div>
          {% if d.temperature is not none %}
            <div class="sensor-value temp-value">
              {{ "%.1f"|format(d.temperature) }}<span class="sensor-unit"> °C</span>
            </div>
          {% else %}
            <div class="no-data">—</div>
          {% endif %}
        </div>

        <!-- Humedad -->
        <div class="sensor-block">
          <div class="sensor-icon">💧</div>
          <div class="sensor-label">Humedad</div>
          {% if d.humidity is not none %}
            <div class="sensor-value hum-value">
              {{ "%.1f"|format(d.humidity) }}<span class="sensor-unit"> %</span>
            </div>
          {% else %}
            <div class="no-data">—</div>
          {% endif %}
        </div>
      </div>

      {% if d.temperature is not none %}
      <div class="comfort-bar-wrap">
        <div class="comfort-label">
          <span>0 °C</span>
          <span>Rango de temperatura</span>
          <span>50 °C</span>
        </div>
        <div class="comfort-bar">
          <div class="comfort-fill" style="width: {{ [[d.temperature / 50 * 100, 0]|max, 100]|min }}%"></div>
        </div>
      </div>
      {% endif %}
    </div>
    {% endfor %}
  </div>

  <!-- BOTONES -->
  <div class="actions">
    <a class="btn btn-primary" href="/download.csv">⬇️ Descargar CSV</a>
    <a class="btn btn-outline" href="/">🔄 Actualizar ahora</a>
  </div>

  <!-- TABLA DETALLADA -->
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Dispositivo</th>
          <th>Modelo</th>
          <th>MAC</th>
          <th>Temperatura (°C)</th>
          <th>Humedad (%)</th>
        </tr>
      </thead>
      <tbody>
        {% for d in devices %}
        <tr>
          <td>{{ d.name }}</td>
          <td>{{ d.model }}</td>
          <td style="font-family:monospace; font-size:0.8rem">{{ d.device }}</td>
          <td>
            {% if d.temperature is not none %}
              <span class="val-temp">{{ "%.2f"|format(d.temperature) }} °C</span>
            {% else %}
              <span class="na">No disponible</span>
            {% endif %}
          </td>
          <td>
            {% if d.humidity is not none %}
              <span class="val-hum">{{ "%.2f"|format(d.humidity) }} %</span>
            {% else %}
              <span class="na">No disponible</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <footer>Govee Sensor Monitor — Datos en tiempo real via Govee API · Última actualización: automática cada 30 s</footer>
</body>
</html>
"""

@app.route("/")
def dashboard():
    devices = get_devices_and_states()
    return render_template_string(HTML_TEMPLATE, devices=devices)

@app.route("/download.csv")
def download_csv():
    devices = get_devices_and_states()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "device", "model", "temperature_c", "humidity_pct"])

    for d in devices:
        writer.writerow([
            d["name"],
            d["device"],
            d["model"],
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
