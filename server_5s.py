"""
5S Audit Command Center — OPEX
Flask backend that reads/writes the `accl_5s_audit_entries` table
on the Azure PostgreSQL server. Replaces the original Supabase REST layer.

Run:
    pip install flask flask-cors psycopg2-binary
    set PGUSER=deputy.coo@akijresource.com
    set PGPASSWORD=<your-password>
    set PGHOST=arl-community-developer.postgres.database.azure.com
    set PGPORT=5432
    set PGDATABASE=ArlOpexDB
    python server_5s.py
Then open http://127.0.0.1:5000
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

# UI shape -> table column (matches the HTML COL map / Supabase columns)
COLUMNS = [
    "id", "audit_no", "audit_timestamp", "audit_place", "auditor_name",
    "auditor_enroll", "sort_removed", "sort_redtag", "order_location",
    "order_labels", "shine_clean", "shine_assigned", "standardize", "sustain",
    "remarks", "created_at", "sbu", "submitter_email", "picture_links", "final_score_pct",
]
# columns we accept on insert (id, audit_timestamp, created_at auto-set)
INSERT_COLUMNS = [
    "audit_no", "sbu", "audit_place", "auditor_name", "auditor_enroll",
    "sort_removed", "sort_redtag", "order_location", "order_labels",
    "shine_clean", "shine_assigned", "standardize", "sustain",
    "remarks", "picture_links",
]


def get_conn():
    return psycopg2.connect(**DB)


def row_to_dict(row):
    out = {}
    for i, name in enumerate(COLUMNS):
        if i >= len(row):
            break
        out[name] = row[i]
    # Normalise timestamp for the UI (Supabase returned ISO string)
    if out.get("audit_timestamp") is not None and not isinstance(out["audit_timestamp"], str):
        out["audit_timestamp"] = out["audit_timestamp"].isoformat()
    if out.get("created_at") is not None and not isinstance(out["created_at"], str):
        out["created_at"] = out["created_at"].isoformat()
    if out.get("picture_links") is not None and not isinstance(out["picture_links"], list):
        out["picture_links"] = list(out["picture_links"])
    return out


def compute_final(s):
    """Return (total, percent) like the front-end, else (None, None)."""
    def avg(a, b):
        try:
            v = [float(a), float(b)]
        except (TypeError, ValueError):
            return None
        return sum(v) / len(v)

    pillars = [
        avg(s.get("sort_removed"), s.get("sort_redtag")),
        avg(s.get("order_location"), s.get("order_labels")),
        avg(s.get("shine_clean"), s.get("shine_assigned")),
        _num(s.get("standardize")),
        _num(s.get("sustain")),
    ]
    if any(p is None for p in pillars):
        return None, None
    total = round(sum(pillars), 2)
    percent = round(total / 25 * 100, 2)
    return total, percent


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@app.route("/")
def index():
    return app.send_static_file("5S Audit Command Center.html")


@app.route("/api/entries", methods=["GET"])
def list_entries():
    limit = request.args.get("limit", type=int)
    if not limit or limit <= 0:
        limit = 1000
    sql = """SELECT {0} FROM public.accl_5s_audit_entries
             ORDER BY audit_timestamp DESC NULLS LAST, id DESC
             LIMIT %s""".format(", ".join(COLUMNS))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/entries", methods=["POST"])
def create_entry():
    body = request.get_json(silent=True) or {}
    required = ["audit_no", "sbu", "audit_place", "auditor_name"]
    for f in required:
        if not body.get(f):
            return jsonify({"error": f + " is required"}), 400

    total, percent = compute_final(body)
    insert = {}
    for c in INSERT_COLUMNS:
        insert[c] = body.get(c)

    placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
    sql = "INSERT INTO public.accl_5s_audit_entries ({0}) VALUES ({1}) RETURNING {2}".format(
        ", ".join(INSERT_COLUMNS), placeholders, ", ".join(COLUMNS)
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [insert[c] for c in INSERT_COLUMNS])
            row = cur.fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/entries/<int:row_id>", methods=["DELETE"])
def delete_entry(row_id):
    sql = "DELETE FROM public.accl_5s_audit_entries WHERE id = %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (row_id,))
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": row_id})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
