"""
SBU KPI Monthly Target Entry Sheet — OPEX
Flask backend that reads/writes the `kpi_target` table on the Azure
PostgreSQL server. Replaces the original Supabase REST layer.

Run:
    pip install flask flask-cors psycopg2-binary
    set PGUSER=deputy.coo@akijresource.com
    set PGPASSWORD=<your-password>
    set PGHOST=arl-community-developer.postgres.database.azure.com
    set PGPORT=5432
    set PGDATABASE=ArlOpexDB
    python server_kpi.py
Then open http://127.0.0.1:5002
"""

import os
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

COLUMNS = ["id", "sbu", "kpi_id", "kpi_label", "unit", "month", "monthly_target", "created_at"]


def get_conn():
    return psycopg2.connect(**DB)


def row_to_dict(row):
    return {COLUMNS[i]: row[i] for i in range(len(COLUMNS))}


@app.route("/")
def index():
    return app.send_static_file("KPI_Target_Entry_Sheet.html")


@app.route("/api/entries", methods=["GET"])
def list_entries():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT {0} FROM public.kpi_target ORDER BY id::int".format(", ".join(COLUMNS))
            )
            rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/entries", methods=["POST"])
def create_entry():
    body = request.get_json(silent=True) or {}
    required = ["sbu", "kpi_id", "month", "monthly_target"]
    for f in required:
        if not body.get(f):
            return jsonify({"error": f + " is required"}), 400

    sbu = body["sbu"]
    kpi_id = body["kpi_id"]
    month = body["month"]
    kpi_label = body.get("kpi_label", "")
    unit = body.get("unit", "")
    target = body["monthly_target"]

    # manual upsert: no unique constraint on the table, so check-then-write
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM public.kpi_target WHERE sbu=%s AND kpi_id=%s AND month=%s",
                (sbu, kpi_id, month),
            )
            existing = cur.fetchone()
            if existing:
                row_id = existing[0]
                cur.execute(
                    "UPDATE public.kpi_target SET kpi_label=%s, unit=%s, monthly_target=%s WHERE id=%s",
                    (kpi_label, unit, target, row_id),
                )
            else:
                row_id = f"{sbu}-{kpi_id}-{month}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                cur.execute(
                    "INSERT INTO public.kpi_target (id, sbu, kpi_id, kpi_label, unit, month, monthly_target, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (row_id, sbu, kpi_id, kpi_label, unit, month, target, datetime.utcnow().isoformat() + "+00"),
                )

            cur.execute(
                "SELECT {0} FROM public.kpi_target WHERE id=%s".format(", ".join(COLUMNS)),
                (row_id,),
            )
            row = cur.fetchone()
    return jsonify(row_to_dict(row)), 200


@app.route("/api/entries/<row_id>", methods=["DELETE"])
def delete_entry(row_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.kpi_target WHERE id=%s", (row_id,))
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": row_id})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5002, debug=True)
