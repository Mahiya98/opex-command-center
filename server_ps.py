"""
Process Standardization Index — Supabase-compatible REST gateway.
Serves process_standardization_index.html and mimics the subset of the
Supabase PostgREST API the front-end uses, backed by the AKIJ Resource
PostgreSQL server (ArlOpexDB), table `process_standardization`.

Run:
    set PGUSER/... then: python server_ps.py
Then open http://127.0.0.1:5007
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

TABLE = "process_standardization"
# whitelisted columns accepted on write
WRITABLE = ["id", "start_date", "sbu", "project_name", "status",
            "is_finalized", "summary", "created_at"]
AUTO = {"id", "created_at"}


def get_conn():
    return psycopg2.connect(**DB)


def build_where(args):
    clauses = []
    params = []
    for key, val in args.items():
        if key in ("select", "order", "limit", "offset"):
            continue
        if not key.isidentifier():
            continue
        if "." not in val:
            clauses.append(f"{key} = %s")
            params.append(val)
            continue
        op, value = val.split(".", 1)
        if op in ("eq",):
            clauses.append(f"{key} = %s")
            params.append(value)
        elif op == "neq":
            clauses.append(f"{key} <> %s")
            params.append(value)
        elif op == "gte":
            clauses.append(f"{key} >= %s")
            params.append(value)
        elif op == "lte":
            clauses.append(f"{key} <= %s")
            params.append(value)
        elif op == "lt":
            clauses.append(f"{key} < %s")
            params.append(value)
        elif op == "gt":
            clauses.append(f"{key} > %s")
            params.append(value)
        elif op == "like":
            params.append("%" + value + "%")
            clauses.append(f"{key} ILIKE %s")
        elif op == "is.null":
            clauses.append(f"{key} IS NULL")
    return clauses, params


def build_order(args):
    order_val = args.get("order")
    if not order_val:
        return ""
    orders = []
    for part in order_val.split(","):
        if "." in part:
            col, direction = part.split(".", 1)
            if col.isidentifier():
                orders.append(f"{col} {'DESC' if direction.startswith('desc') else 'ASC'}")
    return " ORDER BY " + ", ".join(orders) if orders else ""


def rows_out(cur):
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for row in rows:
        if "is_finalized" in row and row["is_finalized"] is not None:
            v = str(row["is_finalized"]).strip().lower()
            row["is_finalized"] = v in ("true", "1", "t", "yes", "on")
    return rows


def row_out_one(cur):
    cols = [d[0] for d in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    if "is_finalized" in row and row["is_finalized"] is not None:
        v = str(row["is_finalized"]).strip().lower()
        row["is_finalized"] = v in ("true", "1", "t", "yes", "on")
    return row


def _bool_str(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "false"
    s = str(v).strip().lower()
    return "true" if s in ("true", "1", "t", "yes", "on") else "false"


@app.route("/")
def index():
    return app.send_static_file("process_standardization_index.html")


@app.route("/api/<supa_table>", methods=["GET"])
def get_rows(supa_table):
    if supa_table != "accl_process_standardization":
        return jsonify([])
    args = request.args
    clauses, params = build_where(args)
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    order_sql = build_order(args)
    limit = args.get("limit")
    limit_sql = f" LIMIT {int(limit)}" if limit and limit.isdigit() else ""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM public.{TABLE}{where_sql}{order_sql}{limit_sql}", params)
            return jsonify(rows_out(cur))


@app.route("/api/<supa_table>", methods=["POST"])
def post_rows(supa_table):
    if supa_table != "accl_process_standardization":
        return jsonify({"error": "unknown table"}), 404
    body = request.get_json(silent=True)
    rows = body if isinstance(body, list) else [body]
    results = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for rb in rows:
                if not isinstance(rb, dict):
                    continue
                row = {k: v for k, v in rb.items() if k in WRITABLE}
                if "is_finalized" in row:
                    row["is_finalized"] = _bool_str(row["is_finalized"])
                row.setdefault("id", f"ps-{uuid.uuid4().hex[:12]}")
                row.setdefault("created_at", datetime.utcnow().isoformat() + "+00")
                cols = list(row.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                cur.execute(
                    f"INSERT INTO public.{TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
                    [row[c] for c in cols])
                results.append(row_out_one(cur))
    return jsonify(results)


@app.route("/api/<supa_table>", methods=["PATCH"])
def patch_rows(supa_table):
    if supa_table != "accl_process_standardization":
        return jsonify({"error": "unknown table"}), 404
    args = request.args
    clauses, params = build_where(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    body = request.get_json(silent=True) or {}
    updates = {k: v for k, v in body.items() if k in WRITABLE and k not in AUTO}
    if "is_finalized" in updates:
        updates["is_finalized"] = _bool_str(updates["is_finalized"])
    if not updates:
        return jsonify([])
    set_sql = ", ".join(f"{k} = %s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE public.{TABLE} SET {set_sql} WHERE {' AND '.join(clauses)} RETURNING *",
                        list(updates.values()) + params)
            return jsonify(rows_out(cur))


@app.route("/api/<supa_table>", methods=["DELETE"])
def delete_rows(supa_table):
    if supa_table != "accl_process_standardization":
        return jsonify({"error": "unknown table"}), 404
    args = request.args
    clauses, params = build_where(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM public.{TABLE} WHERE {' AND '.join(clauses)}", params)
    return jsonify([])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5007, debug=True)
