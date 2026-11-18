from datetime import timedelta

import click
from flask import current_app
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from common import now, stamp
from db import row, write_transaction

DEMO_ACCOUNTS = (
    ("examiner", "Dr Meera Rao", "examiner", None, "Computer Science", "active"),
    ("examiner2", "Dr Arun Sen", "examiner", None, "Computer Science", "active"),
    ("pending", "Nisha Verma", "examiner", None, "Computing", "pending"),
    ("student", "Riya Das", "student", "DEMO01", None, "active"),
    ("student2", "Aman Shah", "student", "DEMO02", None, "active"),
)
DEMO_EXAM_MINUTES = 30


def ensure_admin():
    if row("SELECT id FROM users WHERE role = 'admin'"):
        return
    password = generate_password_hash(current_app.config["ADMIN_PASSWORD"])
    with write_transaction() as db:
        if not db.execute("SELECT id FROM users WHERE role = 'admin'").fetchone():
            db.execute(
                "INSERT INTO users (name, email, password_hash, role, status) VALUES (?, ?, ?, 'admin', 'active')",
                ("Portal Administrator", current_app.config["ADMIN_EMAIL"], password),
            )


@click.command("seed-demo")
@with_appcontext
def seed_demo():
    today = now().replace(hour=10, minute=0, second=0, microsecond=0)
    password = generate_password_hash("DemoPass123")
    with write_transaction() as db:
        if row("SELECT id FROM courses WHERE code = 'DEMO101'"):
            click.echo("Demo data already exists. Use a clean database for fresh demo dates.")
            return
        users = add_demo_accounts(db, password)
        course_id = db.execute(
            "INSERT INTO courses (code, name, description) VALUES (?, ?, ?)",
            ("DEMO101", "Application Development", "Practical demonstrations and viva examinations."),
        ).lastrowid
        examiners = (users["examiner"], users["examiner2"])
        viva_id = add_demo_exam(
            db, course_id, examiners, "Application Demo Viva", "booking_open", today, (-10, -3, -2, 1),
        )
        add_demo_exam(
            db, course_id, examiners, "Database Practical", "slot_creation", today, (-2, 2, 3, 5),
        )
        past_exam_id = add_demo_exam(
            db, course_id, examiners, "Web Programming Practical", "closed", today, (-18, -14, -13, -7),
        )
        for hour, capacity in ((10, 1), (11, 3)):
            start = (today + timedelta(days=3)).replace(hour=hour)
            add_demo_slot(db, viva_id, users["examiner"], start, capacity)
        add_demo_results(db, past_exam_id, today, users)
    click.echo("Demo data added. Sign in with a demo account listed in README.md.")


def add_demo_accounts(db, password_hash):
    user_ids = {}
    for username, name, role, roll_number, department, status in DEMO_ACCOUNTS:
        email = f"{username}@example.test"
        db.execute(
            """
            INSERT INTO users (name, email, password_hash, role, roll_no, department, status)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(email) DO NOTHING
            """,
            (name, email, password_hash, role, roll_number, department, status),
        )
        user = row("SELECT id, role FROM users WHERE email = ?", (email,))
        if user["role"] != role:
            raise click.ClickException("A demo email is already in use by a different role.")
        user_ids[username] = user["id"]
    return user_ids


def add_demo_exam(db, course_id, examiners, title, status, today, window_offsets):
    windows = [stamp(today + timedelta(days=offset)) for offset in window_offsets]
    exam_id = db.execute(
        """
        INSERT INTO exams (course_id, title, duration, max_marks, status,
                           slot_open, slot_close, booking_open, booking_close)
        VALUES (?, ?, ?, 20, ?, ?, ?, ?, ?)
        """,
        (course_id, title, DEMO_EXAM_MINUTES, status, *windows),
    ).lastrowid
    db.executemany(
        "INSERT INTO exam_examiners (exam_id, examiner_id) VALUES (?, ?)",
        [(exam_id, examiner_id) for examiner_id in examiners],
    )
    db.executemany(
        """
        INSERT INTO rubrics (exam_id, criterion, description, max_marks, weight)
        VALUES (?, ?, ?, 10, 50)
        """,
        [
            (exam_id, "Implementation", "Core workflow and behavior."),
            (exam_id, "Explanation", "Reasoning and answers during the viva."),
        ],
    )
    return exam_id


def add_demo_slot(db, exam_id, examiner_id, start, capacity):
    end = start + timedelta(minutes=DEMO_EXAM_MINUTES)
    return db.execute(
        "INSERT INTO slots (exam_id, examiner_id, start_at, end_at, capacity) VALUES (?, ?, ?, ?, ?)",
        (exam_id, examiner_id, stamp(start), stamp(end), capacity),
    ).lastrowid


def add_demo_results(db, exam_id, today, users):
    examiner_id = users["examiner"]
    start = today - timedelta(days=4)
    slot_id = add_demo_slot(db, exam_id, examiner_id, start, capacity=2)
    booked_at = stamp(today - timedelta(days=9))

    # Leave one booking unmarked so the examiner can use it in the demo.
    db.execute(
        "INSERT INTO bookings (student_id, exam_id, slot_id, booked_at) VALUES (?, ?, ?, ?)",
        (users["student2"], exam_id, slot_id, booked_at),
    )
    booking_id = db.execute(
        """
        INSERT INTO bookings (student_id, exam_id, slot_id, booked_at, status)
        VALUES (?, ?, ?, ?, 'completed')
        """,
        (users["student"], exam_id, slot_id, booked_at),
    ).lastrowid
    db.execute(
        """
        INSERT INTO evaluations (booking_id, examiner_id, total, remarks, marked_at)
        VALUES (?, ?, 16, ?, ?)
        """,
        (
            booking_id,
            examiner_id,
            "The app ran correctly. Explain the database links in more detail.",
            stamp(start + timedelta(hours=1)),
        ),
    )
    marks_by_criterion = {"Implementation": 9, "Explanation": 7}
    criteria = db.execute("SELECT id, criterion FROM rubrics WHERE exam_id = ?", (exam_id,))
    db.executemany(
        "INSERT INTO evaluation_scores (booking_id, rubric_id, marks) VALUES (?, ?, ?)",
        [(booking_id, item["id"], marks_by_criterion[item["criterion"]]) for item in criteria],
    )
