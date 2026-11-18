import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from auth import check_role, create_account
from common import FormError, field, like_pattern, number, stamp
from db import get_db, row, rows, write_transaction
from schedule import (
    BOOKING_SELECT, EXAM_SELECT, EXAM_STATUSES, SLOT_SELECT, booking_by_id, exam_by_id,
    replace_examiner, reschedule_booking, rubric_for, rubric_is_ready, save_exam, scores_for, slot_by_id,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
def check_admin():
    return check_role("admin")


def search_term():
    return request.args.get("q", "").strip()[:120]


@bp.get("/")
def dashboard():
    counts = row("""
        SELECT (SELECT COUNT(*) FROM courses) AS courses,
               (SELECT COUNT(*) FROM exams) AS exams,
               (SELECT COUNT(*) FROM users WHERE role = 'examiner') AS examiners,
               (SELECT COUNT(*) FROM users WHERE role = 'student') AS students,
               (SELECT COUNT(*) FROM slots) AS slots,
               (SELECT COUNT(*) FROM bookings) AS bookings
    """)
    pending = row("SELECT COUNT(*) AS total FROM users WHERE role = 'examiner' AND status = 'pending'")["total"]
    latest = rows(BOOKING_SELECT + " ORDER BY b.id DESC LIMIT 6")
    return render_template("admin/dashboard.html", counts=counts, pending=pending, bookings=latest)


@bp.get("/courses")
def courses():
    return render_template("admin/courses.html", courses=rows("SELECT * FROM courses ORDER BY active DESC, code"))


@bp.route("/courses/new", methods=["GET", "POST"])
@bp.route("/courses/<int:course_id>", methods=["GET", "POST"])
def course_form(course_id=None):
    course = row("SELECT * FROM courses WHERE id = ?", (course_id,)) if course_id else {}
    if course is None:
        abort(404)
    if request.method == "POST":
        try:
            values = (field(request.form, "code", "Course code", 30).upper(),
                      field(request.form, "name", "Course name"),
                      field(request.form, "description", "Description", 1000, required=False),
                      int(request.form.get("active") == "1"))
            db = get_db()
            if course_id:
                db.execute("UPDATE courses SET code = ?, name = ?, description = ?, active = ? WHERE id = ?",
                           (*values, course_id))
            else:
                db.execute("INSERT INTO courses (code, name, description, active) VALUES (?, ?, ?, ?)", values)
        except FormError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("This course code is already in use.", "error")
        else:
            flash("Course saved.")
            return redirect(url_for("admin.courses"))
    return render_template("admin/course_form.html", course=course, values=request.form if request.method == "POST" else course)


@bp.post("/courses/<int:course_id>/remove")
def remove_course(course_id):
    with write_transaction() as db:
        if not row("SELECT id FROM courses WHERE id = ?", (course_id,)):
            abort(404)
        if row("SELECT 1 FROM exams WHERE course_id = ?", (course_id,)):
            db.execute("UPDATE courses SET active = 0 WHERE id = ?", (course_id,))
            flash("Course archived. Its examinations and history are still available.")
        else:
            db.execute("DELETE FROM courses WHERE id = ?", (course_id,))
            flash("Course removed.")
    return redirect(url_for("admin.courses"))


@bp.get("/exams")
def exams():
    term = search_term()
    pattern = like_pattern(term)
    exams = rows(EXAM_SELECT + """
        WHERE e.title LIKE ? ESCAPE '\\' OR c.code LIKE ? ESCAPE '\\' OR CAST(e.id AS TEXT) = ?
        ORDER BY e.id DESC
    """, (pattern, pattern, term))
    return render_template("admin/exams.html", exams=exams, q=term)


@bp.get("/exams/<int:exam_id>")
def exam_detail(exam_id):
    exam = exam_by_id(exam_id)
    assigned = rows("""
        SELECT u.id, u.name, u.status FROM exam_examiners a JOIN users u ON u.id = a.examiner_id
        WHERE a.exam_id = ? ORDER BY u.name
    """, (exam_id,))
    available = rows("""
        SELECT id, name FROM users WHERE role = 'examiner' AND status = 'active'
        AND id NOT IN (SELECT examiner_id FROM exam_examiners WHERE exam_id = ?) ORDER BY name
    """, (exam_id,))
    return render_template("admin/exam_detail.html", exam=exam, rubrics=rubric_for(exam_id),
                           assigned=assigned, available=available, statuses=EXAM_STATUSES)


def change_status(db, exam_id, status):
    if status not in EXAM_STATUSES:
        raise FormError("Choose a valid examination status.")
    exam = exam_by_id(exam_id)
    if status in ("slot_creation", "booking_open") and not exam["course_active"]:
        raise FormError("Activate the course before opening this examination.")
    if status == "booking_open" and not rubric_is_ready(rubric_for(exam_id)):
        raise FormError("Set rubric weights to a total of 100% before opening booking.")
    if status == "completed" and row("SELECT 1 FROM bookings WHERE exam_id = ? AND status = 'booked'", (exam_id,)):
        raise FormError("Finish marking current bookings before completing this examination.")
    if status != "completed" and exam["status"] == "completed":
        raise FormError("A completed examination stays in the historical records.")
    db.execute("UPDATE exams SET status = ? WHERE id = ?", (status, exam_id))


@bp.post("/exams/<int:exam_id>/status")
def exam_status(exam_id):
    try:
        with write_transaction() as db:
            change_status(db, exam_id, request.form.get("status"))
    except FormError as error:
        flash(str(error), "error")
    else:
        flash("Examination status updated.")
    return redirect(url_for("admin.exam_detail", exam_id=exam_id))


@bp.route("/exams/new", methods=["GET", "POST"])
@bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
def exam_form(exam_id=None):
    exam = exam_by_id(exam_id) if exam_id is not None else {}
    if request.method == "POST":
        try:
            saved_id = save_exam(request.form, exam_id)
        except FormError as error:
            flash(str(error), "error")
        else:
            flash("Examination saved.")
            return redirect(url_for("admin.exam_detail", exam_id=saved_id))

    return render_template(
        "admin/exam_form.html",
        exam=exam,
        courses=rows("SELECT * FROM courses ORDER BY code"),
        values=request.form if request.method == "POST" else exam,
    )


@bp.post("/exams/<int:exam_id>/remove")
def remove_exam(exam_id):
    with write_transaction() as db:
        exam = exam_by_id(exam_id)
        if exam["status"] != "draft" or row("SELECT 1 FROM slots WHERE exam_id = ?", (exam_id,)):
            flash("Only a draft examination without slots can be removed.", "error")
            return redirect(url_for("admin.exam_detail", exam_id=exam_id))
        db.execute("DELETE FROM exam_examiners WHERE exam_id = ?", (exam_id,))
        db.execute("DELETE FROM rubrics WHERE exam_id = ?", (exam_id,))
        db.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    flash("Examination removed.")
    return redirect(url_for("admin.exams"))


@bp.post("/exams/<int:exam_id>/assignment")
def assignment(exam_id):
    try:
        examiner_id = number(request.form, "examiner_id", "Examiner", maximum=2**63 - 1)
        with write_transaction() as db:
            exam = exam_by_id(exam_id)
            if exam["status"] == "completed":
                raise FormError("Assignments are fixed for completed examinations.")
            if request.form.get("action") == "remove":
                if row("SELECT 1 FROM slots WHERE exam_id = ? AND examiner_id = ?", (exam_id, examiner_id)):
                    raise FormError("This examiner has slot records. Reassign open slots from the slots page.")
                db.execute("DELETE FROM exam_examiners WHERE exam_id = ? AND examiner_id = ?", (exam_id, examiner_id))
            else:
                if not row("SELECT 1 FROM users WHERE id = ? AND role = 'examiner' AND status = 'active'", (examiner_id,)):
                    raise FormError("Choose an active examiner.")
                db.execute("INSERT OR IGNORE INTO exam_examiners (exam_id, examiner_id) VALUES (?, ?)",
                           (exam_id, examiner_id))
    except FormError as error:
        flash(str(error), "error")
    else:
        flash("Examiner assignment updated.")
    return redirect(url_for("admin.exam_detail", exam_id=exam_id))


@bp.route("/exams/<int:exam_id>/rubrics/new", methods=["GET", "POST"])
@bp.route("/exams/<int:exam_id>/rubrics/<int:rubric_id>", methods=["GET", "POST"])
def rubric_form(exam_id, rubric_id=None):
    exam = exam_by_id(exam_id)
    rubric = row("SELECT * FROM rubrics WHERE exam_id = ? AND id = ?", (exam_id, rubric_id)) if rubric_id else {}
    if rubric is None:
        abort(404)
    if request.method == "POST":
        try:
            with write_transaction() as db:
                current = exam_by_id(exam_id)
                if current["status"] == "completed" or row(
                        "SELECT 1 FROM bookings WHERE exam_id = ? AND status = 'completed'", (exam_id,)):
                    raise FormError("The rubric is fixed once marks have been submitted or the exam is complete.")
                form = request.form
                if rubric_id and form.get("action") == "remove":
                    db.execute("DELETE FROM rubrics WHERE exam_id = ? AND id = ?", (exam_id, rubric_id))
                else:
                    values = (field(form, "criterion", "Criterion"), field(form, "description", "Description", 1000, False),
                              number(form, "max_marks", "Maximum marks"), number(form, "weight", "Weight", 1, 100))
                    if rubric_id:
                        db.execute("UPDATE rubrics SET criterion = ?, description = ?, max_marks = ?, weight = ? WHERE id = ? AND exam_id = ?",
                                   (*values, rubric_id, exam_id))
                    else:
                        db.execute("INSERT INTO rubrics (criterion, description, max_marks, weight, exam_id) VALUES (?, ?, ?, ?, ?)",
                                   (*values, exam_id))
                if current["status"] == "booking_open" and not rubric_is_ready(rubric_for(exam_id)):
                    raise FormError("Close booking before changing the rubric's total weight.")
        except FormError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("A criterion with that name already exists.", "error")
        else:
            flash("Rubric updated.")
            return redirect(url_for("admin.exam_detail", exam_id=exam_id))
    return render_template("admin/rubric_form.html", exam=exam, rubric=rubric,
                           values=request.form if request.method == "POST" else rubric)


@bp.get("/people")
def people():
    term = search_term()
    role = request.args.get("role", "examiner")
    if role not in ("examiner", "student"):
        role = "examiner"
    pattern = like_pattern(term)
    users = rows("""
        SELECT * FROM users WHERE role = ? AND
        (name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' OR roll_no LIKE ? ESCAPE '\\' OR CAST(id AS TEXT) = ?)
        ORDER BY status = 'pending' DESC, name
    """, (role, pattern, pattern, pattern, term))
    return render_template("admin/people.html", users=users, q=term, role=role)


@bp.route("/people/add-examiner", methods=["GET", "POST"])
def add_examiner():
    if request.method == "POST":
        try:
            create_account(request.form, "examiner", "active")
        except FormError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("That email is already registered.", "error")
        else:
            flash("Examiner added and activated.")
            return redirect(url_for("admin.people"))
    return render_template("register.html", role="examiner", admin_mode=True)


@bp.post("/people/<int:user_id>/status")
def person_status(user_id):
    status = request.form.get("status")
    if status not in ("active", "inactive"):
        abort(400)
    cursor = get_db().execute("UPDATE users SET status = ? WHERE id = ? AND role = 'examiner'", (status, user_id))
    if not cursor.rowcount:
        abort(404)
    flash("Examiner status updated.")
    return redirect(url_for("admin.people"))


@bp.get("/slots")
def slots():
    exam_id = request.args.get("exam", type=int)
    items = rows(SLOT_SELECT + " WHERE (? IS NULL OR s.exam_id = ?) ORDER BY s.start_at DESC", (exam_id, exam_id))
    return render_template("admin/slots.html", slots=items, exams=rows("SELECT id, title FROM exams ORDER BY title"),
                           exam_id=exam_id)


@bp.route("/slots/<int:slot_id>", methods=["GET", "POST"])
def slot_detail(slot_id):
    slot = slot_by_id(slot_id)
    if request.method == "POST":
        try:
            examiner_id = number(request.form, "examiner_id", "Examiner", maximum=2**63 - 1)
            replace_examiner(slot_id, examiner_id)
        except FormError as error:
            flash(str(error), "error")
        else:
            flash("Slot examiner updated.")
        return redirect(url_for("admin.slot_detail", slot_id=slot_id))
    examiners = rows("SELECT id, name FROM users WHERE role = 'examiner' AND status = 'active' ORDER BY name")
    bookings = rows(BOOKING_SELECT + " WHERE b.slot_id = ? ORDER BY b.id DESC", (slot_id,))
    return render_template("admin/slot_detail.html", slot=slot, examiners=examiners, bookings=bookings)


@bp.get("/bookings")
def bookings():
    term = search_term()
    pattern = like_pattern(term)
    items = rows(BOOKING_SELECT + """
        WHERE u.name LIKE ? ESCAPE '\\' OR e.title LIKE ? ESCAPE '\\' OR u.roll_no LIKE ? ESCAPE '\\'
           OR CAST(b.id AS TEXT) = ? ORDER BY b.id DESC
    """, (pattern, pattern, pattern, term))
    return render_template("admin/bookings.html", bookings=items, q=term)


@bp.route("/bookings/<int:booking_id>", methods=["GET", "POST"])
def booking_detail(booking_id):
    booking = booking_by_id(booking_id)
    if request.method == "POST":
        try:
            slot_id = number(request.form, "slot_id", "Slot", maximum=2**63 - 1)
            reschedule_booking(booking_id, slot_id)
        except FormError as error:
            flash(str(error), "error")
        else:
            flash("Booking rescheduled.")
            return redirect(url_for("admin.bookings"))
    options = rows(SLOT_SELECT + """
        WHERE s.exam_id = ? AND s.id != ? AND s.start_at > ?
          AND s.status = 'open' AND u.status = 'active' AND seats_left > 0 ORDER BY s.start_at
    """, (booking["exam_id"], booking["slot_id"], stamp()))
    return render_template("booking_detail.html", booking=booking, scores=scores_for(booking_id), options=options)
