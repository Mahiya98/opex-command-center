"""
Project Command Center — Supabase-compatible REST + auth gateway.

Serves project-command-center.html and mimics the subset of the Supabase
PostgREST + Auth API the obfuscated front-end uses, backed by the AKIJ
Resource PostgreSQL server (ArlOpexDB), table `projects`.

Mimics:
  GET    /rest/v1/accl_projects?select=*&order=id.asc[&<col>=eq.<val>]
  POST   /rest/v1/accl_projects                  body: JSON row (Prefer: return=representation)
  PATCH  /rest/v1/accl_projects?<col>=eq.<val>   body: JSON update
  DELETE /rest/v1/accl_projects?<col>=eq.<val>
  POST   /auth/v1/token?grant_type=password      body: {email,password} -> {access_token,...}

Run:
    set PGUSER/... then: python server_proj.py
Then open http://127.0.0.1:5009
"""

import json
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

TABLE = "projects"
# whitelisted write columns (id/created_at auto-managed)
WRITABLE = ["project_name", "sbu", "start_date", "end_date", "description", "benefits",
            "expected_cost_savings", "estimated_budget", "status", "milestones",
            "progress", "actual_savings", "actual_budget", "actual_details"]
AUTO = {"id", "created_at"}

# admin credentials (kept simple / local; matches the front-end admin email)
ADMIN_EMAIL = "opex@command-center.internal"
ADMIN_PASSWORD = "opex-admin-2026"


def get_conn():
    return psycopg2.connect(**DB)


def col_value(v):
    """Serialize dicts/lists to JSON text (milestones), else passthrough."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if v is None or v == "null":
        return None
    return str(v)


def build_where(args):
    clauses, params = [], []
    for key, val in args.items():
        if key in ("select", "order", "limit", "offset"):
            continue
        if not key.isidentifier() or "." not in val:
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


def rows_dicts(cur):
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        row = dict(zip(cols, r))
        for k in ("milestones",):
            if k in row and row[k]:
                try:
                    row[k] = json.loads(row[k])
                except (ValueError, TypeError):
                    pass
        out.append(row)
    return out


@app.route("/")
def index():
    return app.send_static_file("project-command-center.html")


# ---------------- auth ----------------
@app.route("/auth/v1/token", methods=["POST"])
def auth_token():
    body = request.get_json(silent=True) or {}
    email = body.get("email")
    password = body.get("password")
    if email != ADMIN_EMAIL:
        return jsonify({"error": "Unknown admin ID", "error_description": "Unknown admin ID"}), 400
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Invalid credentials", "error_description": "Invalid login credentials"}), 400
    return jsonify({
        "access_token": "local-" + uuid.uuid4().hex,
        "token_type": "bearer",
        "expires_in": 3600,
    })


# ---------------- data ----------------
@app.route("/rest/v1/accl_projects", methods=["GET"])
def get_rows():
    args = request.args
    clauses, params = build_where(args)
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    order_sql = build_order(args)
    limit = args.get("limit")
    limit_sql = f" LIMIT {int(limit)}" if limit and limit.isdigit() else ""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM public.{TABLE}{where_sql}{order_sql}{limit_sql}", params)
            return jsonify(rows_dicts(cur))


@app.route("/rest/v1/accl_projects", methods=["POST"])
def post_rows():
    body = request.get_json(silent=True)
    rows = body if isinstance(body, list) else [body]
    results = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for rb in rows:
                if not isinstance(rb, dict):
                    continue
                row = {k: col_value(v) for k, v in rb.items() if k in WRITABLE}
                row["id"] = "prj-" + uuid.uuid4().hex[:12]
                row.setdefault("created_at", datetime.utcnow().isoformat() + "+00")
                cols = list(row.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                cur.execute(
                    f"INSERT INTO public.{TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
                    [row[c] for c in cols])
                results.append(dict(zip([d[0] for d in cur.description], cur.fetchone())))
    return jsonify(results)


@app.route("/rest/v1/accl_projects", methods=["PATCH"])
def patch_rows():
    args = request.args
    clauses, params = build_where(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    body = request.get_json(silent=True) or {}
    updates = {k: col_value(v) for k, v in body.items() if k in WRITABLE and k not in AUTO}
    if not updates:
        return jsonify([])
    set_sql = ", ".join(f"{k} = %s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE public.{TABLE} SET {set_sql} WHERE {' AND '.join(clauses)} RETURNING *",
                        list(updates.values()) + params)
            data = rows_dicts(cur)
    return jsonify(data)


@app.route("/rest/v1/accl_projects", methods=["DELETE"])
def delete_rows():
    args = request.args
    clauses, params = build_where(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM public.{TABLE} WHERE {' AND '.join(clauses)}", params)
    return jsonify([])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5009, debug=True)
