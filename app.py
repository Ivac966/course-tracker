import sqlite3
import os
import csv
import io
from datetime import date, datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, g, session, Response
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
DB_PATH = os.path.join(os.path.dirname(__file__), "course_tracker.db")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        db.executescript(f.read())
    db.commit()
    db.close()


def seed_data():
    db = get_db()

    db.execute(
        "INSERT OR IGNORE INTO admin (username, password) VALUES (?, ?)",
        ("admin", generate_password_hash("school123")),
    )
    db.commit()

    groups = ["Computer Science", "Biology"]
    for g_name in groups:
        db.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", (g_name,))
    db.commit()

    common_subjects = ["English", "Maths", "Pak Studies", "Islamiyat", "Urdu", "Chemistry", "Physics"]
    for s_name in common_subjects:
        db.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", (s_name,))
    db.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", ("Computer Science",))
    db.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", ("Biology",))
    db.commit()

    cs_group_id = db.execute("SELECT id FROM groups WHERE name = ?", ("Computer Science",)).fetchone()["id"]
    bio_group_id = db.execute("SELECT id FROM groups WHERE name = ?", ("Biology",)).fetchone()["id"]

    cs_subjects = common_subjects + ["Computer Science"]
    bio_subjects = common_subjects + ["Biology"]

    for s_name in cs_subjects:
        subj_id = db.execute("SELECT id FROM subjects WHERE name = ?", (s_name,)).fetchone()["id"]
        db.execute("INSERT OR IGNORE INTO group_subjects (group_id, subject_id) VALUES (?, ?)", (cs_group_id, subj_id))

    for s_name in bio_subjects:
        subj_id = db.execute("SELECT id FROM subjects WHERE name = ?", (s_name,)).fetchone()["id"]
        db.execute("INSERT OR IGNORE INTO group_subjects (group_id, subject_id) VALUES (?, ?)", (bio_group_id, subj_id))

    db.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM admin WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password"], password):
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("dashboard"))
        error = "Invalid username or password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    db = get_db()
    groups = db.execute("SELECT * FROM groups").fetchall()
    today = date.today().isoformat()

    group_data = []
    total_chapters = 0
    total_done = 0
    behind_count = 0

    for grp in groups:
        subjects = db.execute(
            """
            SELECT s.id, s.name FROM subjects s
            JOIN group_subjects gs ON gs.subject_id = s.id
            WHERE gs.group_id = ?
            """,
            (grp["id"],),
        ).fetchall()

        subject_rows = []
        for subj in subjects:
            chapters = db.execute(
                "SELECT id, deadline FROM chapters WHERE subject_id = ?", (subj["id"],)
            ).fetchall()
            chapter_ids = [c["id"] for c in chapters]
            total = len(chapter_ids)

            done = 0
            overdue = 0
            if total:
                placeholders = ",".join("?" * len(chapter_ids))
                done = db.execute(
                    f"SELECT COUNT(*) as c FROM progress WHERE group_id = ? AND chapter_id IN ({placeholders}) AND completed_date IS NOT NULL",
                    (grp["id"], *chapter_ids),
                ).fetchone()["c"]

                completed_ids = {
                    r["chapter_id"] for r in db.execute(
                        f"SELECT chapter_id FROM progress WHERE group_id = ? AND chapter_id IN ({placeholders}) AND completed_date IS NOT NULL",
                        (grp["id"], *chapter_ids),
                    ).fetchall()
                }
                for ch in chapters:
                    if ch["id"] not in completed_ids and ch["deadline"] and ch["deadline"] < today:
                        overdue += 1

            pct = round((done / total) * 100) if total else 0
            subject_rows.append({
                "id": subj["id"], "name": subj["name"], "done": done, "total": total,
                "pct": pct, "overdue": overdue,
            })

            total_chapters += total
            total_done += done
            if total and (pct < 50 or overdue > 0):
                behind_count += 1

        group_data.append({"id": grp["id"], "name": grp["name"], "subjects": subject_rows})

    overall_pct = round((total_done / total_chapters) * 100) if total_chapters else 0

    return render_template(
        "dashboard.html",
        groups=group_data,
        overall_pct=overall_pct,
        total_done=total_done,
        total_chapters=total_chapters,
        behind_count=behind_count,
    )


@app.route("/subject/<int:group_id>/<int:subject_id>")
@login_required
def subject_view(group_id, subject_id):
    db = get_db()
    group = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    subject = db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    chapters = db.execute(
        "SELECT * FROM chapters WHERE subject_id = ? ORDER BY order_num", (subject_id,)
    ).fetchall()

    today = date.today().isoformat()
    chapter_rows = []
    for ch in chapters:
        prog = db.execute(
            "SELECT completed_date FROM progress WHERE group_id = ? AND chapter_id = ?",
            (group_id, ch["id"]),
        ).fetchone()
        completed = prog is not None and prog["completed_date"] is not None
        chapter_rows.append({
            "id": ch["id"],
            "name": ch["name"],
            "deadline": ch["deadline"],
            "completed": completed,
            "completed_date": prog["completed_date"] if prog else None,
            "overdue": (not completed) and bool(ch["deadline"]) and ch["deadline"] < today,
        })

    return render_template(
        "subject.html", group=group, subject=subject, chapters=chapter_rows
    )


@app.route("/mark/<int:group_id>/<int:chapter_id>", methods=["POST"])
@login_required
def mark_chapter(group_id, chapter_id):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM progress WHERE group_id = ? AND chapter_id = ?", (group_id, chapter_id)
    ).fetchone()

    now = datetime.now().isoformat(timespec="seconds")

    if existing and existing["completed_date"]:
        db.execute(
            "UPDATE progress SET completed_date = NULL WHERE group_id = ? AND chapter_id = ?",
            (group_id, chapter_id),
        )
        action = "unmarked"
    elif existing:
        db.execute(
            "UPDATE progress SET completed_date = ? WHERE group_id = ? AND chapter_id = ?",
            (date.today().isoformat(), group_id, chapter_id),
        )
        action = "completed"
    else:
        db.execute(
            "INSERT INTO progress (group_id, chapter_id, completed_date) VALUES (?, ?, ?)",
            (group_id, chapter_id, date.today().isoformat()),
        )
        action = "completed"

    db.execute(
        "INSERT INTO progress_history (group_id, chapter_id, action, action_time) VALUES (?, ?, ?, ?)",
        (group_id, chapter_id, action, now),
    )
    db.commit()

    subject_id = db.execute(
        "SELECT subject_id FROM chapters WHERE id = ?", (chapter_id,)
    ).fetchone()["subject_id"]

    return redirect(url_for("subject_view", group_id=group_id, subject_id=subject_id))


@app.route("/add_chapter/<int:subject_id>", methods=["POST"])
@login_required
def add_chapter(subject_id):
    db = get_db()
    name = request.form.get("name", "").strip()
    deadline = request.form.get("deadline", "").strip() or None

    if name:
        max_order = db.execute(
            "SELECT MAX(order_num) as m FROM chapters WHERE subject_id = ?", (subject_id,)
        ).fetchone()["m"]
        next_order = (max_order or 0) + 1
        db.execute(
            "INSERT INTO chapters (subject_id, name, order_num, deadline) VALUES (?, ?, ?, ?)",
            (subject_id, name, next_order, deadline),
        )
        db.commit()

    group_id = request.form.get("group_id")
    return redirect(url_for("subject_view", group_id=group_id, subject_id=subject_id))


@app.route("/bulk_add_chapters/<int:subject_id>", methods=["POST"])
@login_required
def bulk_add_chapters(subject_id):
    db = get_db()
    raw = request.form.get("bulk_names", "")
    names = [line.strip() for line in raw.splitlines() if line.strip()]

    max_order = db.execute(
        "SELECT MAX(order_num) as m FROM chapters WHERE subject_id = ?", (subject_id,)
    ).fetchone()["m"]
    next_order = (max_order or 0) + 1

    for name in names:
        db.execute(
            "INSERT INTO chapters (subject_id, name, order_num) VALUES (?, ?, ?)",
            (subject_id, name, next_order),
        )
        next_order += 1
    db.commit()

    group_id = request.form.get("group_id")
    return redirect(url_for("subject_view", group_id=group_id, subject_id=subject_id))


@app.route("/history")
@login_required
def history():
    db = get_db()
    rows = db.execute(
        """
        SELECT ph.action, ph.action_time, c.name as chapter_name,
               s.name as subject_name, gr.name as group_name
        FROM progress_history ph
        JOIN chapters c ON c.id = ph.chapter_id
        JOIN subjects s ON s.id = c.subject_id
        JOIN groups gr ON gr.id = ph.group_id
        ORDER BY ph.action_time DESC
        LIMIT 200
        """
    ).fetchall()
    return render_template("history.html", rows=rows)


@app.route("/export")
@login_required
def export_report():
    db = get_db()
    rows = db.execute(
        """
        SELECT gr.name as group_name, s.name as subject_name, c.name as chapter_name,
               c.deadline, p.completed_date
        FROM chapters c
        JOIN subjects s ON s.id = c.subject_id
        JOIN group_subjects gs ON gs.subject_id = s.id
        JOIN groups gr ON gr.id = gs.group_id
        LEFT JOIN progress p ON p.chapter_id = c.id AND p.group_id = gr.id
        ORDER BY gr.name, s.name, c.order_num
        """
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Group", "Subject", "Chapter", "Deadline", "Completed On"])
    for r in rows:
        writer.writerow([
            r["group_name"], r["subject_name"], r["chapter_name"],
            r["deadline"] or "-", r["completed_date"] or "Not completed",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=course_coverage_report.csv"},
    )


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        init_db()
        with app.app_context():
            seed_data()
    app.run(debug=True)