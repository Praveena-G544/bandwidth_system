import csv, io
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, flash, session
from config import DB

app = Flask(__name__)
app.secret_key = "bandwidth-demo"

def connect():
    """Open a connection to the chosen data source: public = stored data, sim = simulated data."""
    schema = "sim" if session.get("mode") == "sim" else "public"
    return psycopg2.connect(**DB, options="-c search_path=" + schema)

@app.context_processor
def inject_mode():
    return {"mode": session.get("mode", "real")}

@app.route("/mode/<m>")
def set_mode(m):
    session["mode"] = "sim" if m == "sim" else "real"
    return redirect(url_for("dashboard"))

LEVELS = {"small": 500, "medium": 3000, "large": 15000}

@app.route("/simulate", methods=["POST"])
def simulate():
    """Fills ONLY the sim schema. The stored (public) data is never touched."""
    if session.get("mode") != "sim":
        flash("Switch to Simulated data first.")
        return redirect(url_for("dashboard"))
    action = request.form.get("action", "tick")
    top = LEVELS.get(request.form.get("level"), 3000)
    if action == "reset":
        run("TRUNCATE usage_records, alerts RESTART IDENTITY", fetch=False)
        msg = "Simulation cleared."
    elif action == "history":
        run("""INSERT INTO usage_records(device_id, usage_date, download_mb, upload_mb)
               SELECT d.device_id, g::date, round((100 + random()*%s)::numeric,2), round((20 + random()*%s/5)::numeric,2)
               FROM devices d, generate_series(CURRENT_DATE - 29, CURRENT_DATE - 1, INTERVAL '1 day') g
               ON CONFLICT (device_id, usage_date) DO NOTHING""", (top / 10, top / 10), fetch=False)
        msg = "Added 30 days of simulated history."
    else:
        run("""INSERT INTO usage_records(device_id, usage_date, download_mb, upload_mb)
               SELECT device_id, CURRENT_DATE, round((random()*%s)::numeric,2), round((random()*%s/5)::numeric,2) FROM devices
               ON CONFLICT (device_id, usage_date) DO UPDATE
               SET download_mb = usage_records.download_mb + EXCLUDED.download_mb,
                   upload_mb = usage_records.upload_mb + EXCLUDED.upload_mb""", (top, top), fetch=False)
        msg = "Simulated one round of usage for every device."
    if request.form.get("ajax"):
        return "ok"
    flash(msg)
    return redirect(request.referrer or url_for("dashboard"))

def run(sql, params=(), fetch=True):
    """Run one statement in its own transaction."""
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

def num(rows):
    return [{k: (float(v) if hasattr(v, "as_tuple") else (str(v) if hasattr(v, "isoformat") else v))
             for k, v in r.items()} for r in rows]

@app.route("/")
def dashboard():
    c = run("""SELECT (SELECT COUNT(*) FROM users) users, (SELECT COUNT(*) FROM devices) devices,
               (SELECT COUNT(*) FROM alerts) alerts,
               (SELECT COALESCE(round(SUM(download_mb+upload_mb)/1024,1),0) FROM usage_records
                 WHERE usage_date >= date_trunc('month', CURRENT_DATE)) gb""")[0]
    top = num(run("""SELECT full_name, used_gb, quota_gb, pct FROM v_monthly_user_usage
                     WHERE month = date_trunc('month', CURRENT_DATE)::date ORDER BY used_gb DESC"""))
    daily = num(run("""SELECT usage_date AS day, round(SUM(download_mb+upload_mb)/1024,2) AS gb
                       FROM usage_records WHERE usage_date >= CURRENT_DATE-29
                       GROUP BY usage_date ORDER BY usage_date"""))
    return render_template("dashboard.html", c=c, top=top, daily=daily)

@app.route("/users", methods=["GET", "POST"])
def users():
    if request.method == "POST":
        try:
            run("INSERT INTO users(full_name,email,plan_id) VALUES (%s,%s,%s)",
                (request.form["full_name"], request.form["email"], request.form["plan_id"]), fetch=False)
            flash("User added.")
        except psycopg2.Error as e:
            flash("Could not add user: " + e.pgerror.splitlines()[0])
        return redirect(url_for("users"))
    rows = run("""SELECT u.user_id,u.full_name,u.email,p.plan_name,p.monthly_quota_gb,
                  (SELECT COUNT(*) FROM devices d WHERE d.user_id=u.user_id) devices
                  FROM users u JOIN plans p ON p.plan_id=u.plan_id ORDER BY u.user_id""")
    return render_template("users.html", rows=rows, plans=run("SELECT * FROM plans ORDER BY plan_id"))

@app.route("/users/<int:i>/delete", methods=["POST"])
def delete_user(i):
    run("DELETE FROM users WHERE user_id=%s", (i,), fetch=False)
    flash("User deleted.")
    return redirect(url_for("users"))

@app.route("/devices", methods=["GET", "POST"])
def devices():
    if request.method == "POST":
        try:
            run("INSERT INTO devices(user_id,device_name,mac_address) VALUES (%s,%s,%s)",
                (request.form["user_id"], request.form["device_name"], request.form["mac_address"].upper()), fetch=False)
            flash("Device added.")
        except psycopg2.Error as e:
            flash("Could not add device: " + e.pgerror.splitlines()[0])
        return redirect(url_for("devices"))
    rows = run("""SELECT d.device_id,d.device_name,d.mac_address,u.full_name FROM devices d
                  JOIN users u ON u.user_id=d.user_id ORDER BY d.device_id""")
    return render_template("devices.html", rows=rows, users=run("SELECT user_id,full_name FROM users ORDER BY 2"))

@app.route("/devices/<int:i>/delete", methods=["POST"])
def delete_device(i):
    run("DELETE FROM devices WHERE device_id=%s", (i,), fetch=False)
    flash("Device deleted.")
    return redirect(url_for("devices"))

@app.route("/usage", methods=["GET", "POST"])
def usage():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".csv"):
            flash("Choose a .csv file.")
            return redirect(url_for("usage"))
        conn = connect()
        try:
            with conn:  # all-or-nothing transaction
                with conn.cursor() as cur:
                    n = 0
                    for r in csv.DictReader(io.StringIO(f.read().decode("utf-8-sig"))):
                        cur.execute("""INSERT INTO usage_records(device_id,usage_date,download_mb,upload_mb)
                            SELECT device_id,%s,%s,%s FROM devices WHERE mac_address=%s
                            ON CONFLICT (device_id,usage_date) DO UPDATE
                            SET download_mb=EXCLUDED.download_mb, upload_mb=EXCLUDED.upload_mb""",
                            (r["usage_date"], r["download_mb"], r["upload_mb"], r["mac_address"].strip().upper()))
                        if cur.rowcount == 0:
                            raise ValueError("Unknown MAC address: " + r["mac_address"])
                        n += 1
            flash(f"Imported {n} rows.")
        except Exception as e:
            flash("Import cancelled, nothing saved. " + str(e).splitlines()[0])
        finally:
            conn.close()
        return redirect(url_for("usage"))
    rows = run("""SELECT r.usage_date,u.full_name,d.device_name,r.download_mb,r.upload_mb FROM usage_records r
                  JOIN devices d ON d.device_id=r.device_id JOIN users u ON u.user_id=d.user_id
                  ORDER BY r.usage_date DESC, r.usage_id DESC LIMIT 50""")
    return render_template("usage.html", rows=rows)

@app.route("/alerts")
def alerts():
    rows = run("""SELECT a.created_at,u.full_name,a.threshold,a.message FROM alerts a
                  JOIN users u ON u.user_id=a.user_id ORDER BY a.created_at DESC, a.threshold DESC""")
    return render_template("alerts.html", rows=rows)

if __name__ == "__main__":
    app.run(debug=True)
