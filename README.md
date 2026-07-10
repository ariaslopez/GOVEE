# Govee Dashboard (simple)

Small Flask app to visualize Govee devices using the official cloud API
and download a CSV report with current device states.

## Setup

1. Request your Govee API key from the Govee app.
2. Create a `.env` file based on `.env.example` and set `GOVEE_API_KEY`.
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Run the app:

   ```bash
   python app.py
   ```

5. Open `http://localhost:5000` (or `http://SERVER_IP:5000` from other
   devices in the same LAN).
