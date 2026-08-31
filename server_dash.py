"""
Daily Operation Meeting Dashboard — AROPEX
Flask backend that reads data for the dashboard from the Azure PostgreSQL
server (ArlOpexDB). Replaces the original Supabase REST layer.

Tables used (Supabase name -> Postgres table):
  'daily meeting target'        -> daily_meeting_target
  'daily meeting form'          -> daily_meeting_form
  'qcp_audit'                   -> qcp_audit
  'accl_5s_audit_entries'       -> accl_5s_audit_entries
  'accl_improvement_cards'      -> improvement_cards
  'accl_problem_solving_cards'  -> problem_solving_cards
  'accl_projects'               -> projects

Run:
    pip install flask flask-cors psycopg2-binary
    set PGUSER=deputy.coo@akijresource.com
    set PGPASSWORD=<your-password>
    set PGHOST=arl-community-developer.postgres.database.azure.com
    set PGPORT=5432
    set PGDATABASE=ArlOpexDB
    python server_dash.py
Then open http://127.0.0.1:5003
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


def get_conn():
    return psycopg2.connect(**DB)


def query(sql, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def count_query(sql, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()[0]


def iso(v):
    """Normalise a date/timestamp value to ISO string for the UI."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return v.isoformat()


@app.route("/")
def index():
    return app.send_static_file("daily-meeting-dashboard.html")


@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    month_start = request.args.get("monthStart")
    today = request.args.get("today")
    yesterday = request.args.get("yesterday")
    today_next = request.args.get("todayNext")  # today + 1 day, exclusive upper bound for 5s

    d_out = {}

    # ---- 1. targets ----
    target_rows = query("SELECT sbu, production_tracking, qcp_audit, kaizen, problem_solving, "
                        "\"5s_floor_audit\", daily_operation_meeting, daily_improvement_meeting "
                        "FROM public.daily_meeting_target")
    d_out["targets"] = target_rows

    # ---- 2. daily meeting forms (month + yesterday) ----
    form_cols = "sbu, meeting_date, daily_operation_meeting, daily_improvement_meeting, four_h_tracking_meeting_count"
    d_out["formMonth"] = query(
        "SELECT {0} FROM public.daily_meeting_form "
        "WHERE meeting_date::date >= %s AND meeting_date::date <= %s".format(form_cols),
        (month_start, today))
    d_out["formYesterday"] = query(
        "SELECT {0} FROM public.daily_meeting_form WHERE meeting_date::date = %s".format(form_cols),
        (yesterday,))

    # ---- 3. qcp_audit (month + yesterday) ----
    d_out["qcpMonth"] = query(
        "SELECT sbu, audit_date FROM public.qcp_audit "
        "WHERE audit_date::date >= %s AND audit_date::date <= %s", (month_start, today))
    d_out["qcpYesterday"] = query(
        "SELECT sbu, audit_date FROM public.qcp_audit WHERE audit_date::date = %s", (yesterday,))

    # ---- 4. 5s audits (month + yesterday) ----
    d_out["fiveSMonth"] = query(
        "SELECT sbu, audit_timestamp, final_score_pct FROM public.accl_5s_audit_entries "
        "WHERE audit_timestamp >= %s AND audit_timestamp < %s", (month_start, today_next))
    d_out["fiveSYesterday"] = query(
        "SELECT sbu, audit_timestamp, final_score_pct FROM public.accl_5s_audit_entries "
        "WHERE audit_timestamp >= %s AND audit_timestamp < %s", (yesterday, today))

    # ---- 5. kaizen: improvement_cards ----
    d_out["kaizenOngoing"] = count_query(
        "SELECT count(*) FROM public.improvement_cards WHERE status='Ongoing'")
    d_out["kaizenMonth"] = query(
        "SELECT sbu, status, created_date FROM public.improvement_cards "
        "WHERE status='Initiated' AND created_date::date >= %s AND created_date::date <= %s",
        (month_start, today))
    d_out["kaizenYest"] = query(
        "SELECT sbu, status, created_date FROM public.improvement_cards "
        "WHERE status='Initiated' AND created_date::date <= %s "
        "ORDER BY created_date DESC", (today,))
    d_out["kaizenRejected"] = count_query(
        "SELECT count(*) FROM public.improvement_cards "
        "WHERE status='Rejected' AND created_date::date >= %s AND created_date::date <= %s",
        (month_start, today))
    d_out["kaizenCompleted"] = count_query(
        "SELECT count(*) FROM public.improvement_cards "
        "WHERE status='Completed' AND created_date::date >= %s AND created_date::date <= %s",
        (month_start, today))

    # ---- 6. problem solving: problem_solving_cards ----
    d_out["psOngoing"] = count_query(
        "SELECT count(*) FROM public.problem_solving_cards WHERE status='Ongoing'")
    d_out["psMonth"] = query(
        "SELECT sbu, status, created_date FROM public.problem_solving_cards "
        "WHERE status='Initiated' AND created_date::date >= %s AND created_date::date <= %s",
        (month_start, today))
    d_out["psYest"] = query(
        "SELECT sbu, status, created_date FROM public.problem_solving_cards "
        "WHERE status='Initiated' AND created_date::date <= %s "
        "ORDER BY created_date DESC", (today,))
    d_out["psRejected"] = count_query(
        "SELECT count(*) FROM public.problem_solving_cards "
        "WHERE status='Rejected' AND created_date::date >= %s AND created_date::date <= %s",
        (month_start, today))
    d_out["psCompleted"] = count_query(
        "SELECT count(*) FROM public.problem_solving_cards "
        "WHERE status='Completed' AND created_date::date >= %s AND created_date::date <= %s",
        (month_start, today))

    # ---- 7. overdue alerts ----
    d_out["overdue"] = query(
        "SELECT id, sbu, meeting_date, previous_agenda_corrective_actions, "
        "target_completion_date, status FROM public.daily_meeting_form "
        "WHERE target_completion_date IS NOT NULL AND target_completion_date::date < %s "
        "AND status <> 'Completed' ORDER BY target_completion_date ASC LIMIT 6", (today,))

    # ---- 8. recent projects ----
    d_out["projects"] = query(
        "SELECT id, sbu, project_name, end_date, progress, status "
        "FROM public.projects ORDER BY created_at DESC LIMIT 20")

    # extra: the dashboard loads the SBU list from the target table
    d_out["sbuList"] = [r["sbu"] for r in target_rows]

    # normalise dates for UI
    for key in ("formMonth", "formYesterday"):
        for r in d_out[key]:
            r["meeting_date"] = iso(r["meeting_date"])
    for key in ("qcpMonth", "qcpYesterday"):
        for r in d_out[key]:
            r["audit_date"] = iso(r["audit_date"])
    for key in ("fiveSMonth", "fiveSYesterday"):
        for r in d_out[key]:
            r["audit_timestamp"] = iso(r["audit_timestamp"])
    for key in ("kaizenMonth", "kaizenYest", "psMonth", "psYest"):
        for r in d_out[key]:
            r["created_date"] = iso(r["created_date"])
    for r in d_out["overdue"]:
        r["meeting_date"] = iso(r["meeting_date"])
        r["target_completion_date"] = iso(r["target_completion_date"])
    for r in d_out["projects"]:
        r["end_date"] = iso(r["end_date"])

    return jsonify(d_out)


@app.route("/api/agenda", methods=["GET"])
def agenda():
    search = request.args.get("search", "")
    sbu = request.args.get("sbu", "")
    status = request.args.get("status", "")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    overdue = request.args.get("overdue", "") == "true"
    today = request.args.get("today", "")
    page = int(request.args.get("page", 0))
    size = int(request.args.get("size", 6))

    where = ["previous_agenda_corrective_actions IS NOT NULL"]
    params = []

    if search:
        where.append("(previous_agenda_corrective_actions ILIKE %s OR corrective_action_owner ILIKE %s)")
        like = "%" + search + "%"
        params.extend([like, like])
    if sbu:
        where.append("sbu = %s")
        params.append(sbu)
    if status:
        where.append("status = %s")
        params.append(status)
    if date_from:
        where.append("meeting_date::date >= %s")
        params.append(date_from)
    if date_to:
        where.append("meeting_date::date <= %s")
        params.append(date_to)
    if overdue:
        where.append("target_completion_date IS NOT NULL AND target_completion_date::date < %s AND status <> 'Completed'")
        params.append(today)

    where_sql = " AND ".join(where)

    total = count_query(
        "SELECT count(*) FROM public.daily_meeting_form WHERE " + where_sql, params)

    rows = query(
        "SELECT id, sbu, meeting_date, previous_agenda_corrective_actions, "
        "target_completion_date, status, corrective_action_owner, corrective_action_due_date "
        "FROM public.daily_meeting_form WHERE " + where_sql +
        " ORDER BY meeting_date DESC LIMIT %s OFFSET %s",
        params + [size, page * size])

    for r in rows:
        r["meeting_date"] = iso(r["meeting_date"])
        r["target_completion_date"] = iso(r["target_completion_date"])
        r["corrective_action_due_date"] = iso(r["corrective_action_due_date"])

    return jsonify({"rows": rows, "count": total})


@app.route("/api/agenda/status-chips", methods=["GET"])
def status_chips():
    out = {}
    for status in ("Open", "In Progress", "Completed"):
        out[status] = count_query(
            "SELECT count(*) FROM public.daily_meeting_form "
            "WHERE previous_agenda_corrective_actions IS NOT NULL AND status = %s", (status,))
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5003, debug=True)
