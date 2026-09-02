#!/usr/bin/env python3
"""
OPEX AI Alert Agent — 5S & QCP below-daily-target email alert.

Runs on a schedule (GitHub Actions cron). Pulls TODAY's live data from
Supabase, compares against the daily target (5S >= 70%, QCP >= monthly/30),
and emails sanjeed@akijshipping.com from mahiya@akijresource.com when any
SBU is below target.

Secrets come from environment variables (GitHub Actions secrets), never here.

Env vars:
  SUPABASE_URL      e.g. https://vpwbcuxwxkqvauffqooj.supabase.co
  SUPABASE_ANON     Supabase anon/publishable key
  SMTP_HOST         smtp.gmail.com
  SMTP_PORT         465
  SMTP_USER         mahiya@akijresource.com
  SMTP_PASS         <Gmail App Password>
  MAIL_TO           sanjeed@akijshipping.com
  MAIL_FROM         mahiya@akijresource.com
"""

import json
import os
import smtplib
import ssl
import urllib.request
from datetime import date, timedelta

# ------- config from env -------
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON = os.environ["SUPABASE_ANON"]
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "mahiya@akijresource.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_TO = os.environ.get("MAIL_TO", "sanjeed@akijshipping.com")
MAIL_FROM = os.environ.get("MAIL_FROM", "mahiya@akijresource.com")

FIVE_TARGET = 70.0        # daily 5S target (%)
QCP_DAYS = 30             # monthly QCP target divided by 30
QCP_FALLBACK = 3.0        # daily fallback if no target found

today = date.today()
today_str = today.isoformat()
tomorrow_str = (today + timedelta(days=1)).isoformat()


def sb_get(table, query):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_ANON, "Authorization": f"Bearer {SUPABASE_ANON}"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- 5S: avg score per SBU today (by audit_timestamp) ----------
try:
    s5 = sb_get("accl_5s_audit_entries",
                f"select=sbu,final_score_pct&audit_timestamp=gte.{today_str}"
                f"&audit_timestamp=lt.{tomorrow_str}&limit=1000")
except Exception as e:
    s5 = []
    print("5S fetch error:", e)

five_by = {}
for r in s5:
    sbu = (r.get("sbu") or "").strip()
    if sbu:
        five_by.setdefault(sbu, []).append(float(r.get("final_score_pct") or 0))
five_avg = {k: sum(v) / len(v) for k, v in five_by.items()}

# ---------- QCP: count per SBU today (by audit_date) ----------
try:
    qcp = sb_get("qcp_audit",
                 f"select=sbu&audit_date=gte.{today_str}&audit_date=lte.{today_str}&limit=2000")
except Exception as e:
    qcp = []
    print("QCP fetch error:", e)

qcp_count = {}
for r in qcp:
    sbu = (r.get("sbu") or "").strip()
    if sbu:
        qcp_count[sbu] = qcp_count.get(sbu, 0) + 1

# ---------- monthly targets per SBU (5s_score, qcp_audit) ----------
month_str = f"{today.year}-{today.month:02d}"
try:
    tgts = sb_get("ACCL_KPI_TARGET", "select=sbu,kpi_id,month,monthly_target&limit=2000")
except Exception as e:
    tgts = []
    print("target fetch error:", e)

def month_ok(t):
    # match e.g. "Aug-26" or "August" against current month
    if not t:
        return False
    return (month_str in t) or (t.split("-")[0] in ["January","February","March","April","May","June","July","August","September","October","November","December"]
                                and today.strftime("%B") == t)

tmap = {}
for t in tgts:
    if month_ok(t.get("month")):
        sbu = (t.get("sbu") or "").strip()
        tmap.setdefault(sbu, {})[t.get("kpi_id")] = float(
            str(t.get("monthly_target") or 0).replace(",", "") or 0)

# ---------- build alerts ----------
today_label = today.strftime("%d %B %Y")
lines = [f"OPEX Alert — 5S & QCP below target — {today_label}", ""]
below = []

sbus = sorted(set(list(five_avg.keys()) + list(qcp_count.keys()) + list(tmap.keys())))

for sbu in sbus:
    # 5S
    s5t = tmap.get(sbu, {}).get("5s_score") or FIVE_TARGET
    s5a = five_avg.get(sbu)
    # QCP daily target = monthly qcp_audit / 30
    qcpm = tmap.get(sbu, {}).get("qcp_audit") or (QCP_FALLBACK * QCP_DAYS)
    qcp_day_t = qcpm / QCP_DAYS
    qa = qcp_count.get(sbu, 0)

    probs = []
    if s5a is not None and s5a < s5t:
        probs.append(f"5S score {s5a:.1f}% < target {s5t:.0f}%")
    if qa < qcp_day_t:
        probs.append(f"QCP audits {qa} < target {qcp_day_t:.1f}/day")

    if probs:
        below.append(sbu)
        lines.append(f"• {sbu}: " + "; ".join(probs))

if not below:
    print("OK — no SBU below target. No email sent.")
    raise SystemExit(0)

lines.insert(1, "SBU(s) below daily target:")
body = "\n".join(lines)
subject = f"OPEX Alert: below target {today_label} — {', '.join(below)}"

print(body)

# ---------- send email ----------
msg = (
    "From: {frm}\r\n"
    "To: {to}\r\n"
    "Subject: {subj}\r\n"
    "MIME-Version: 1.0\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "{body}"
).format(frm=MAIL_FROM, to=MAIL_TO, subj=subject, body=body)

try:
    if SMTP_PORT == 465:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls(context=ssl.create_default_context())
    server.login(SMTP_USER, SMTP_PASS)
    server.sendmail(MAIL_FROM, [MAIL_TO], msg.encode("utf-8"))
    server.quit()
    print("EMAIL SENT to", MAIL_TO)
except Exception as e:
    print("EMAIL FAILED:", e)
    raise
