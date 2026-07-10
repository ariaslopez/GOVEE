import os
import io
import csv
import requests
from flask import Flask, Response, render_template_string

# Configuración
GOVEE_API_KEY = os.getenv("GOVEE_API_KEY")
BASE_URL = "https://developer-api.govee.com/v1"  # endpoint usado en los ejemplos oficiales

app = Flask(__name__)

def govee_get(path, params=None):
    """Call Govee API with the required Govee-API-Key header."""
    if not GOVEE_API_KEY:
        raise RuntimeError("Missing GOVEE_API_KEY environment variable")
    headers = {"Govee-API-Key": GOVEE_API_KEY}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params or {})
    resp.raise_for_status()
    return resp.json()

def get_devices_and_states():
    """
    Returns a list of devices and their current state.

    NOTE: Adjust JSON keys once you inspect the real API response.
    """
    devices_resp = govee_get("/devices")
    # The exact JSON structure depends on API version; "devices" is used in many examples.
    devices = devices_resp.get("data", {}).get("devices", [])

    results = []
    for d in devices:
        mac = d.get("device")
        model = d.get("model")
        name = d.get("deviceName", "Unnamed device")

        # Current state for each device (endpoint pattern from community examples)
        state_resp = govee_get("/devices", params={"device": mac, "model": model})

        results.append({
            "name": name,
            "device": mac,
            "model": model,
            "raw_state": state_resp,
        })
    return results

HTML_TEMPLATE = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Dashboard Govee simple</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ccc; padding: 0.5rem; text-align: left; }
    th { background: #f5f5f5; }
    .btn { display: inline-block; padding: 0.5rem 1rem; background: #2563eb;
           color: white; text-decoration: none; border-radius: 4px; }
  </style>
</head>
<body>
  <h1>Dashboard Govee (simple)</h1>
  <p>Esta vista muestra tus dispositivos Govee y su estado actual.</p>

  <a class="btn" href="/download.csv">Descargar informe CSV</a>

  <table>
    <thead>
      <tr>
        <th>Nombre</th>
        <th>MAC / Device</th>
        <th>Modelo</th>
        <th>Estado (JSON)</th>
      </tr>
    </thead>
    <tbody>
      {% for d in devices %}
      <tr>
        <td>{{ d.name }}</td>
        <td>{{ d.device }}</td>
        <td>{{ d.model }}</td>
        <td><pre style="white-space: pre-wrap; font-size: 0.8rem;">
{{ d.raw_state | tojson(indent=2) }}
        </pre></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
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
    writer.writerow(["name", "device", "model", "raw_state_json"])

    for d in devices:
        writer.writerow([
            d["name"],
            d["device"],
            d["model"],
            d["raw_state"],
        ])

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=govee_report.csv"}
    )

if __name__ == "__main__":
    # Local server accessible from your LAN
    app.run(host="0.0.0.0", port=5000, debug=False)
