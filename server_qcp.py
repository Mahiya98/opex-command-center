"""
QCP Audit Portal — Supabase-compatible REST gateway.
Serves qcp_audit_portal.html and mimics the subset of the Supabase
PostgREST API the front-end uses, backed by the AKIJ Resource PostgreSQL
server (ArlOpexDB). Tables: qcp_audit, qcp_specs.

Supported:
  GET    /api/<table>?select=*&order=id.desc&limit=N[&<col>=eq./gte./lte.<val>]
  POST   /api/<table>                       body: JSON row / list
  PATCH  /api/<table>?<col>=eq.<val>        body: JSON update
  DELETE /api/<table>?<col>=eq.<val>

Run:
    set PGUSER/... then: python server_qcp.py
Then open http://127.0.0.1:5008
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

ALLOWED_TABLES = {"qcp_audit", "qcp_specs"}
# columns (id/created_at/updated_at are auto-managed)
WRITABLE = {
    "qcp_audit": ["audit_date", "sbu", "section", "machine_name", "product_criteria",
                  "shift", "test_name", "min_value", "max_value", "actual_value",
                  "status", "wss", "remarks", "audit_month"],
    "qcp_specs": ["sbu", "section", "machine", "item", "test_name",
                  "min_value", "max_value"],
}
AUTO = {"id", "created_at", "updated_at"}


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
        if op == "eq":
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


def norm_value(table, col, v):
    """Coerce numeric values appropriately for write."""
    if col in ("min_value", "max_value", "actual_value"):
        if v is None or v == "" or v == "null":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return v
    if v is None or v == "null":
        return None
    return v


def rows_dicts(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


@app.route("/")
def index():
    return app.send_static_file("qcp_audit_portal.html")


@app.route("/api/<table>", methods=["GET"])
def get_rows(table):
    if table not in ALLOWED_TABLES:
        return jsonify([])
    args = request.args
    clauses, params = build_where(args)
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    order_sql = build_order(args)
    limit = args.get("limit")
    limit_sql = f" LIMIT {int(limit)}" if limit and limit.isdigit() else ""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM public.{table}{where_sql}{order_sql}{limit_sql}", params)
            data = rows_dicts(cur)
    return jsonify(data)


@app.route("/api/<table>", methods=["POST"])
def post_rows(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "unknown table"}), 404
    body = request.get_json(silent=True)
    rows = body if isinstance(body, list) else [body]
    allowed = WRITABLE[table]
    results = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for rb in rows:
                if not isinstance(rb, dict):
                    continue
                row = {k: norm_value(table, k, v) for k, v in rb.items() if k in allowed}
                cur.execute(f"SELECT COALESCE(MAX(id),0)+1 FROM public.{table}")
                next_id = cur.fetchone()[0]
                row["id"] = next_id
                now = datetime.utcnow()
                if table == "qcp_audit":
                    row.setdefault("created_at", now)
                else:
                    row.setdefault("created_at", now)
                    row.setdefault("updated_at", now)
                cols = list(row.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                cur.execute(
                    f"INSERT INTO public.{table} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
                    [row[c] for c in cols])
                results.append(dict(zip([d[0] for d in cur.description], cur.fetchone())))
    return jsonify(results)


@app.route("/api/<table>", methods=["PATCH"])
def patch_rows(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "unknown table"}), 404
    args = request.args
    clauses, params = build_where(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    body = request.get_json(silent=True) or {}
    allowed = WRITABLE[table]
    updates = {k: norm_value(table, k, v) for k, v in body.items() if k in allowed and k not in AUTO}
    if table == "qcp_specs":
        updates["updated_at"] = datetime.utcnow()
    if not updates:
        return jsonify([])
    set_sql = ", ".join(f"{k} = %s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE public.{table} SET {set_sql} WHERE {' AND '.join(clauses)} RETURNING *",
                        list(updates.values()) + params)
            data = rows_dicts(cur)
    return jsonify(data)


@app.route("/api/<table>", methods=["DELETE"])
def delete_rows(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "unknown table"}), 404
    args = request.args
    clauses, params = build_where(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM public.{table} WHERE {' AND '.join(clauses)}", params)
    return jsonify([])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5008, debug=True)
