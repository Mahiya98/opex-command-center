"""
4-Hour Production Tracking — OPEX
Flask backend that reads/writes the `four_hour_tracking` table
on the Azure PostgreSQL server.

Run:
    pip install flask psycopg2-binary
    set PGUSER=deputy.coo@akijresource.com
    set PGPASSWORD=<your-password>
    set PGHOST=arl-community-developer.postgres.database.azure.com
    set PGPORT=5432
    set PGDATABASE=ArlOpexDB
    python server.py
Then open http://127.0.0.1:5000
"""

import os
import uuid
from datetime import datetime

import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS

_BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=_BASE)
app.static_url_path = ""
CORS(app)

DB = {
    "host": os.environ.get("PGHOST", "arl-community-developer.postgres.database.azure.com"),
    "port": os.environ.get("PGPORT", "5432"),
    "user": os.environ.get("PGUSER", "deputy.coo@akijresource.com"),
    "password": os.environ.get("PGPASSWORD", ""),
    "dbname": os.environ.get("PGDATABASE", "ArlOpexDB"),
    "connect_timeout": 30,
}

COLUMNS = [
    "id", "entry_date", "shift_name", "time_slot", "line", "sbu", "name", "email",
    "major_breakdown", "absent_department", "notes", "created_at", "submitted_at",
    "target_qty", "actual_qty", "gap_qty",
]


def get_conn():
    return psycopg2.connect(**DB)


def row_to_dict(row):
    return {COLUMNS[i]: row[i] for i in range(len(COLUMNS))}


@app.route("/")
def index():
    return app.send_static_file("4 Hour Tracking.html")


@app.route("/api/entries", methods=["GET"])
def list_entries():
    entry_date = request.args.get("date")
    if not entry_date:
        return jsonify({"error": "date is required"}), 400

    # Normalise to yyyy-mm-dd
    try:
        entry_date = datetime.strptime(entry_date[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return jsonify({"error": "date must be yyyy-mm-dd"}), 400

    sql = "SELECT {0} FROM public.four_hour_tracking WHERE entry_date = %s ORDER BY created_at".format(
        ", ".join(COLUMNS)
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (entry_date,))
            rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/entries", methods=["POST"])
def create_entry():
    body = request.get_json(silent=True) or {}
    required = ["entry_date", "shift_name", "time_slot", "sbu", "name", "email"]
    for f in required:
        if not body.get(f):
            return jsonify({"error": f + " is required"}), 400

    entry = {
        "id": body.get("id") or str(uuid.uuid4()),
        "entry_date": body["entry_date"],
        "shift_name": body["shift_name"],
        "time_slot": body["time_slot"],
        "line": body.get("line", ""),
        "sbu": body["sbu"],
        "name": body["name"],
        "email": body["email"],
        "major_breakdown": body.get("major_breakdown", ""),
        "absent_department": body.get("absent_department", ""),
        "notes": body.get("notes", ""),
        "created_at": body.get("created_at") or datetime.utcnow().isoformat() + "+00",
        "submitted_at": body.get("submitted_at") or datetime.utcnow().isoformat() + "+00",
        "target_qty": body.get("target", ""),
        "actual_qty": body.get("actual", ""),
        "gap_qty": body.get("gap", ""),
    }

    placeholders = ", ".join(["%s"] * len(COLUMNS))
    sql = "INSERT INTO public.four_hour_tracking ({0}) VALUES ({1})".format(
        ", ".join(COLUMNS), placeholders
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [entry[c] for c in COLUMNS])
    return jsonify(entry), 201


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
