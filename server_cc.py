"""
OPEX Command Center — generic Supabase-compatible REST gateway.

Serves the Command Center HTML and mimics the subset of the Supabase
PostgREST API the front-end uses, backed by the AKIJ Resource PostgreSQL
server (ArlOpexDB). The front-end only needs its SUPABASE_URL/KEY changed
to point here.

Supported REST patterns:
  GET    /api/<table>?select=*&order=id.desc&<col>=eq.<val>[&...]
  POST   /api/<table>                      body: JSON row(s)
  PATCH  /api/<table>?<col>=eq.<val>[&...] body: JSON to update
  DELETE /api/<table>?<col>=eq.<val>[&...]

Table name mapping (Supabase -> Postgres):
  accl_improvement_cards     -> improvement_cards
  accl_problem_solving_cards -> problem_solving_cards
  accl_cost_savings          -> cost_savings
  accl_productivity_improvement -> productivity_improvement
  accl_wastage_savings_qty   -> wastage_savings_qty
  accl_environment_impact    -> environment_impact
  accl_time_savings_min      -> (no table; returns empty)

Run:
    set PGUSER/... then: python server_cc.py
Then open http://127.0.0.1:5006
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

# Supabase table name -> Postgres table name
TABLE_MAP = {
    "accl_improvement_cards": "improvement_cards",
    "accl_problem_solving_cards": "problem_solving_cards",
    "accl_cost_savings": "cost_savings",
    "accl_productivity_improvement": "productivity_improvement",
    "accl_wastage_savings_qty": "wastage_savings_qty",
    "accl_environment_impact": "environment_impact",
    "accl_time_savings_min": None,  # not present in this DB
}

# Columns that are auto-managed (never written by the client)
AUTO_COLUMNS = {"id", "created_at", "updated_at"}
INSERTABLE = {  # safe column allow-list per table (whitelisted columns we accept on insert/update)
    "improvement_cards": ["id", "card_no", "sbu", "dept", "provider", "created_date",
                          "details", "benefit", "status", "target_date", "accepted_date",
                          "hidden", "created_at"],
    "problem_solving_cards": ["id", "card_no", "sbu", "dept", "provider", "created_date",
                              "problem_description", "root_cause", "corrective_action",
                              "status", "target_date", "accepted_date", "hidden", "created_at"],
    "cost_savings": ["id", "sl", "source", "card_no", "project_details", "section", "uom",
                     "start_date", "completed_date", "monthly_values", "hidden",
                     "total_savings_bdt", "sbu", "created_at"],
    "productivity_improvement": ["id", "sl", "source", "card_no", "project_details", "section",
                                 "uom", "start_date", "completed_date", "monthly_values",
                                 "hidden", "created_at"],
    "wastage_savings_qty": ["id", "sl", "source", "card_no", "project_details", "section",
                            "uom", "start_date", "completed_date", "monthly_values",
                            "hidden", "created_at"],
    "environment_impact": ["id", "sl", "source", "card_no", "project_details", "section",
                           "uom", "start_date", "completed_date", "monthly_values",
                           "hidden", "created_at"],
}


def get_conn():
    return psycopg2.connect(**DB)


def parse_filters(args):
    """Parse query params into (where_sql, params) for eq/neq/lt/gt/null filters.

    Supabase sends filters as query params, e.g. `?id=eq.12` -> key='id', val='eq.12'.
    """
    clauses = []
    params = []
    for key, val in args.items():
        if key in ("select", "order", "limit", "offset"):
            continue
        if not key.isidentifier():
            continue
        # val format: "eq.<value>" / "neq.<value>" / "lt.<value>" / "is.null"
        if "." not in val:
            clauses.append(f"{key} = %s")
            params.append(val)
            continue
        op, value = val.split(".", 1)
        if op == "is.null" or value == "true" and op == "null":
            clauses.append(f"{key} IS NULL")
        elif op == "is":
            clauses.append(f"{key} IS NULL" if value == "null" else f"{key} = %s")
            if value != "null":
                params.append(value)
        elif op == "eq":
            clauses.append(f"{key} = %s")
            params.append(value)
        elif op == "neq":
            clauses.append(f"{key} <> %s")
            params.append(value)
        elif op == "lt":
            clauses.append(f"{key} < %s")
            params.append(value)
        elif op == "lte":
            clauses.append(f"{key} <= %s")
            params.append(value)
        elif op == "gt":
            clauses.append(f"{key} > %s")
            params.append(value)
        elif op == "gte":
            clauses.append(f"{key} >= %s")
            params.append(value)
        elif op == "like":
            params.append("%" + value + "%")
            clauses.append(f"{key} ILIKE %s")
        elif op == "in":
            vals = value.split(",")
            clauses.append(f"{key} IN ({', '.join(['%s']*len(vals))})")
            params.extend(vals)
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


@app.route("/")
def index():
    return app.send_static_file("opex_command_center.html")


@app.route("/api/<supa_table>", methods=["GET"])
def cc_get(supa_table):
    table = TABLE_MAP.get(supa_table)
    if table is None:
        return jsonify([])
    args = request.args
    clauses, params = parse_filters(args)
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    order_sql = build_order(args)
    limit = args.get("limit")
    limit_sql = f" LIMIT {int(limit)}" if limit and limit.isdigit() else ""

    sql = f"SELECT * FROM public.{table}{where_sql}{order_sql}{limit_sql}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    out = [dict(zip(cols, r)) for r in rows]
    return jsonify(out)


def _normalise(v):
    if isinstance(v, dict):
        return str(v)
    return v


@app.route("/api/<supa_table>", methods=["POST"])
def cc_post(supa_table):
    table = TABLE_MAP.get(supa_table)
    if table is None:
        return jsonify({"error": "unknown table"}), 404
    body = request.get_json(silent=True)
    rows = body if isinstance(body, list) else [body]

    allowed = INSERTABLE[table]
    results = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for rb in rows:
                if not isinstance(rb, dict):
                    continue
                # coerce id if the row wants an auto id
                row = {k: _normalise(v) for k, v in rb.items() if k in allowed}
                now = datetime.utcnow().isoformat() + "+00"
                row.setdefault("created_at", now)

                if table in ("improvement_cards", "problem_solving_cards"):
                    row.setdefault("id", f"ts-{uuid.uuid4().hex[:12]}")
                else:
                    # savings-style tables: id is a string "sl" -> reuse a uuid
                    row.setdefault("id", f"s-{uuid.uuid4().hex[:12]}")

                cols = list(row.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_sql = ", ".join(cols)
                cur.execute(
                    f"INSERT INTO public.{table} ({col_sql}) VALUES ({placeholders}) RETURNING *",
                    [row[c] for c in cols],
                )
                out_cols = [d[0] for d in cur.description]
                res = cur.fetchone()
                results.append(dict(zip(out_cols, res)))
    return jsonify(results)


@app.route("/api/<supa_table>", methods=["PATCH"])
def cc_patch(supa_table):
    table = TABLE_MAP.get(supa_table)
    if table is None:
        return jsonify({"error": "unknown table"}), 404
    args = request.args
    clauses, params = parse_filters(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    where_sql = " AND ".join(clauses)

    body = request.get_json(silent=True) or {}
    allowed = INSERTABLE[table]
    updates = {k: _normalise(v) for k, v in body.items() if k in allowed and k not in AUTO_COLUMNS}
    if not updates:
        return jsonify([])

    set_sql = ", ".join(f"{k} = %s" for k in updates)
    sql = f"UPDATE public.{table} SET {set_sql} WHERE {where_sql} RETURNING *"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, list(updates.values()) + params)
            if cur.description is None:
                return jsonify([])
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    return jsonify([dict(zip(cols, r)) for r in rows])


@app.route("/api/<supa_table>", methods=["DELETE"])
def cc_delete(supa_table):
    table = TABLE_MAP.get(supa_table)
    if table is None:
        return jsonify({"error": "unknown table"}), 404
    args = request.args
    clauses, params = parse_filters(args)
    if not clauses:
        return jsonify({"error": "missing filter"}), 400
    where_sql = " AND ".join(clauses)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM public.{table} WHERE {where_sql}", params)
            deleted = cur.rowcount
    return jsonify([])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5006, debug=True)
