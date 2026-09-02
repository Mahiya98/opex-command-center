"""
OPEX Command Center — landing page launcher.
Serves index.html (module switcher) on port 5004.
"""
import os
from flask import Flask, send_from_directory

_BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=_BASE)
app.static_url_path = ""


@app.route("/")
def index():
    return send_from_directory(_BASE, "index.html")


@app.route("/all")
def index_all():
    return send_from_directory(_BASE, "index-all.html")


@app.route("/memo")
def memo():
    return send_from_directory(_BASE, "daily-ops-memo.html")


@app.route("/dashboard")
def daily_dash():
    return send_from_directory(_BASE, "opex-daily-dashboard.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5004, debug=True)
