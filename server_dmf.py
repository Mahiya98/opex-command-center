"""
Daily Meeting Form — AROPEX
Flask backend that reads/writes the `daily_meeting_form` table on the Azure
PostgreSQL server. Replaces the original Supabase REST layer.

Also serves the per-SBU 4-hour tracking target from `daily_meeting_target`.

Run:
    pip install flask flask-cors psycopg2-binary
    set PGUSER=deputy.coo@akijresource.com
    set PGPASSWORD=<your-password>
    set PGHOST=arl-community-developer.postgres.database.azure.com
    set PGPORT=5432
    set PGDATABASE=ArlOpexDB
    python server_dmf.py
Then open http://127.0.0.1:5005
"""

import os

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
    "id", "meeting_date", "sbu", "daily_operation_meeting",
    "daily_improvement_meeting", "previous_agenda_corrective_actions",
    "four_h_tracking_meeting_count", "submitted_by", "agenda_operation_meeting",
    "target_completion_date", "status", "corrective_action_owner",
    "corrective_action_due_date",
]

INSERT_COLUMNS = [
    "meeting_date", "sbu", "daily_operation_meeting",
    "daily_improvement_meeting", "previous_agenda_corrective_actions",
    "four_h_tracking_meeting_count", "submitted_by", "agenda_operation_meeting",
]


def get_conn():
    return psycopg2.connect(**DB)


def row_to_dict(row):
    return {COLUMNS[i]: row[i] for i in range(len(COLUMNS))}


def iso(v):
    return v.isoformat() if hasattr(v, "isoformat") and not isinstance(v, str) else v


@app.route("/")
def index():
    return app.send_static_file("daily-meeting-form.html")


@app.route("/api/targets", methods=["GET"])
def targets():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sbu, "4h_tracking_meeting" FROM public.daily_meeting_target ORDER BY sbu')
            rows = cur.fetchall()
    out = {}
    for sbu, tgt in rows:
        out[sbu] = tgt
    return jsonify(out)


@app.route("/api/entries", methods=["POST"])
def create_entry():
    body = request.get_json(silent=True) or {}
    required = ["meeting_date", "sbu"]
    for f in required:
        if not body.get(f):
            return jsonify({"error": f + " is required"}), 400

    # manual upsert on (meeting_date, sbu): no unique constraint
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM public.daily_meeting_form WHERE meeting_date=%s AND sbu=%s",
                (body["meeting_date"], body["sbu"]),
            )
            existing = cur.fetchone()
            insert = {}
            for c in INSERT_COLUMNS:
                insert[c] = body.get(c)

            if existing:
                row_id = existing[0]
                for c in INSERT_COLUMNS:
                    if c == "meeting_date" or c == "sbu":
                        continue
                    cur.execute(
                        "UPDATE public.daily_meeting_form SET {0}=%s WHERE id=%s".format(c),
                        (insert[c], row_id),
                    )
            else:
                cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM public.daily_meeting_form")
                next_id = cur.fetchone()[0]
                insert_cols = ["id"] + INSERT_COLUMNS
                values = [next_id] + [insert[c] for c in INSERT_COLUMNS]
                placeholders = ", ".join(["%s"] * len(insert_cols))
                cur.execute(
                    "INSERT INTO public.daily_meeting_form ({0}) VALUES ({1}) RETURNING {2}".format(
                        ", ".join(insert_cols), placeholders, ", ".join(COLUMNS)),
                    values,
                )
                row = cur.fetchone()
                d = row_to_dict(row)
                d["meeting_date"] = iso(d["meeting_date"])
                return jsonify(d), 201

            cur.execute(
                "SELECT {0} FROM public.daily_meeting_form WHERE id=%s".format(", ".join(COLUMNS)),
                (row_id,),
            )
            row = cur.fetchone()
    d = row_to_dict(row)
    d["meeting_date"] = iso(d["meeting_date"])
    return jsonify(d), 200


@app.route("/api/entries/search", methods=["GET"])
def search():
    date_val = request.args.get("date")
    sbu_val = request.args.get("sbu")

    where = []
    params = []
    if date_val:
        where.append("meeting_date = %s")
        params.append(date_val)
    if sbu_val:
        where.append("sbu = %s")
        params.append(sbu_val)
    where_sql = " WHERE " + " AND ".join(where) if where else ""

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT {0} FROM public.daily_meeting_form {1} ORDER BY meeting_date DESC".format(
                    ", ".join(COLUMNS), where_sql),
                params,
            )
            rows = cur.fetchall()

    out = []
    for r in rows:
        d = row_to_dict(r)
        d["meeting_date"] = iso(d["meeting_date"])
        out.append(d)
    return jsonify(out)


@app.route("/api/entries/<int:row_id>", methods=["PATCH"])
def update_entry(row_id):
    body = request.get_json(silent=True) or {}
    allowed = [
        "agenda_operation_meeting", "target_completion_date", "status",
        "corrective_action_owner", "corrective_action_due_date",
    ]
    processed = []
    params = []
    for c in allowed:
        if c in body:
            processed.append(c)
            params.append(body[c])
    if not processed:
        return jsonify({"error": "no updatable fields"}), 400

    set_sql = ", ".join("{0}=%s".format(c) for c in processed)
    params.append(row_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE public.daily_meeting_form SET {0} WHERE id=%s".format(set_sql), params)
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
            cur.execute(
                "SELECT {0} FROM public.daily_meeting_form WHERE id=%s".format(", ".join(COLUMNS)),
                (row_id,),
            )
            row = cur.fetchone()
    d = row_to_dict(row)
    d["meeting_date"] = iso(d["meeting_date"])
    return jsonify(d)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5005, debug=True)
