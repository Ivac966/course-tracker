import sqlite3
import os
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, g

app = Flask(__name__)
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


@app.route("/")
def dashboard():
    db = get_db()
    groups = db.execute("SELECT * FROM groups").fetchall()

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
                "SELECT id FROM chapters WHERE subject_id = ?", (subj["id"],)
            ).fetchall()
            chapter_ids = [c["id"] for c in chapters]
            total = len(chapter_ids)

            done = 0
            if total:
                placeholders = ",".join("?" * len(chapter_ids))
                done = db.execute(
                    f"SELECT COUNT(*) as c FROM progress WHERE group_id = ? AND chapter_id IN ({placeholders}) AND completed_date IS NOT NULL",
                    (grp["id"], *chapter_ids),
                ).fetchone()["c"]

            pct = round((done / total) * 100) if total else 0
            subject_rows.append({"id": subj["id"], "name": subj["name"], "done": done, "total": total, "pct": pct})

            total_chapters += total
            total_done += done
            if total and pct < 50:
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
def subject_view(group_id, subject_id):
    db = get_db()
    group = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    subject = db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    chapters = db.execute(
        "SELECT * FROM chapters WHERE subject_id = ? ORDER BY order_num", (subject_id,)
    ).fetchall()

    chapter_rows = []
    for ch in chapters:
        prog = db.execute(
            "SELECT completed_date FROM progress WHERE group_id = ? AND chapter_id = ?",
            (group_id, ch["id"]),
        ).fetchone()
        chapter_rows.append({
            "id": ch["id"],
            "name": ch["name"],
            "completed": prog is not None and prog["completed_date"] is not None,
            "completed_date": prog["completed_date"] if prog else None,
        })

    return render_template(
        "subject.html", group=group, subject=subject, chapters=chapter_rows
    )


@app.route("/mark/<int:group_id>/<int:chapter_id>", methods=["POST"])
def mark_chapter(group_id, chapter_id):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM progress WHERE group_id = ? AND chapter_id = ?", (group_id, chapter_id)
    ).fetchone()

    if existing and existing["completed_date"]:
        db.execute(
            "UPDATE progress SET completed_date = NULL WHERE group_id = ? AND chapter_id = ?",
            (group_id, chapter_id),
        )
    elif existing:
        db.execute(
            "UPDATE progress SET completed_date = ? WHERE group_id = ? AND chapter_id = ?",
            (date.today().isoformat(), group_id, chapter_id),
        )
    else:
        db.execute(
            "INSERT INTO progress (group_id, chapter_id, completed_date) VALUES (?, ?, ?)",
            (group_id, chapter_id, date.today().isoformat()),
        )
    db.commit()

    subject_id = db.execute(
        "SELECT subject_id FROM chapters WHERE id = ?", (chapter_id,)
    ).fetchone()["subject_id"]

    return redirect(url_for("subject_view", group_id=group_id, subject_id=subject_id))


@app.route("/add_chapter/<int:subject_id>", methods=["POST"])
def add_chapter(subject_id):
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        max_order = db.execute(
            "SELECT MAX(order_num) as m FROM chapters WHERE subject_id = ?", (subject_id,)
        ).fetchone()["m"]
        next_order = (max_order or 0) + 1
        db.execute(
            "INSERT INTO chapters (subject_id, name, order_num) VALUES (?, ?, ?)",
            (subject_id, name, next_order),
        )
        db.commit()

    group_id = request.form.get("group_id")
    return redirect(url_for("subject_view", group_id=group_id, subject_id=subject_id))


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        init_db()
        with app.app_context():
            seed_data()
    app.run(debug=True)