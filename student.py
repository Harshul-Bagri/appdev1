import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import check_role
from common import FormError, email_value, field, like_pattern, stamp
from db import get_db, rows
from schedule import (
    BOOKING_SELECT, EXAM_SELECT, SLOT_SELECT, book_slot, booking_by_id, booking_open,
    cancel_booking, cancelling_allowed, exam_by_id, live_booking, scores_for,
)

bp = Blueprint("student", __name__, url_prefix="/student")


@bp.before_request
def check_student():
    return check_role("student")


@bp.get("/")
def dashboard():
    term = request.args.get("q", "").strip()[:120]
    course_id = request.args.get("course", type=int)
    student_id = g.user["id"]
    time = stamp()
    pattern = like_pattern(term)
    exams = rows(EXAM_SELECT + """
        WHERE c.active = 1 AND e.status IN ('slot_creation', 'booking_open', 'closed')
          AND e.booking_close > ? AND (? IS NULL OR c.id = ?)
          AND (e.title LIKE ? ESCAPE '\\' OR c.code LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\'
               OR CAST(e.id AS TEXT) = ?)
        ORDER BY e.booking_open, e.title
    """, (time, course_id, course_id, pattern, pattern, pattern, term))
    courses = rows("SELECT id, code, name FROM courses WHERE active = 1 ORDER BY code")
    upcoming = rows(BOOKING_SELECT + """
        WHERE b.student_id = ? AND b.status = 'booked' AND s.start_at >= ? ORDER BY s.start_at
    """, (student_id, time))
    slots = rows(SLOT_SELECT + """
        JOIN courses c ON c.id = e.course_id
        WHERE c.active = 1 AND e.status = 'booking_open' AND e.booking_open <= ? AND e.booking_close > ?
          AND s.status = 'open' AND u.status = 'active' AND s.start_at > ? AND seats_left > 0
          AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.student_id = ? AND b.exam_id = e.id
                          AND b.status IN ('booked', 'completed'))
        ORDER BY s.start_at LIMIT 5
    """, (time, time, time, student_id))
    return render_template("student/dashboard.html", exams=exams, courses=courses, upcoming=upcoming, slots=slots,
                           course_id=course_id, q=term, time=time)


@bp.get("/exams/<int:exam_id>")
def exam_detail(exam_id):
    exam = exam_by_id(exam_id)
    existing = live_booking(g.user["id"], exam_id)
    time = stamp()
    visible = (
        exam["course_active"]
        and exam["status"] not in ("draft", "completed")
        and time < exam["booking_close"]
    )
    if not visible and existing is None:
        abort(404)
    slots = rows(SLOT_SELECT + """
        WHERE s.exam_id = ? AND s.status = 'open' AND u.status = 'active' AND s.start_at > ?
        ORDER BY s.start_at, u.name
    """, (exam_id, time))
    return render_template("student/exam_detail.html", exam=exam, slots=slots, existing=existing,
                           open_for_booking=booking_open(exam, time))


@bp.post("/slots/<int:slot_id>/book")
def book(slot_id):
    try:
        exam_id = book_slot(g.user["id"], slot_id)
    except FormError as error:
        flash(str(error), "error")
        return redirect(url_for("student.dashboard"))
    flash("Booking confirmed.")
    return redirect(url_for("student.exam_detail", exam_id=exam_id))


@bp.get("/bookings")
def bookings():
    items = rows(BOOKING_SELECT + " WHERE b.student_id = ? ORDER BY b.id DESC", (g.user["id"],))
    return render_template("student/bookings.html", bookings=items)


@bp.get("/bookings/<int:booking_id>")
def booking_detail(booking_id):
    booking = booking_by_id(booking_id)
    if booking["student_id"] != g.user["id"]:
        abort(404)
    return render_template("booking_detail.html", booking=booking, scores=scores_for(booking_id),
                           can_cancel=cancelling_allowed(booking, stamp()))


@bp.post("/bookings/<int:booking_id>/cancel")
def cancel(booking_id):
    try:
        cancel_booking(booking_id, g.user["id"])
    except FormError as error:
        flash(str(error), "error")
    else:
        flash("Booking cancelled.")
    return redirect(url_for("student.booking_detail", booking_id=booking_id))


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        try:
            name = field(request.form, "name", "Full name")
            email = email_value(request.form)
            roll = field(request.form, "roll_no", "Roll number", 40).upper()
            get_db().execute("UPDATE users SET name = ?, email = ?, roll_no = ? WHERE id = ?",
                             (name, email, roll, g.user["id"]))
        except FormError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("That email or roll number is already registered.", "error")
        else:
            flash("Profile updated.")
            return redirect(url_for("student.profile"))
    return render_template("student/profile.html", values=request.form if request.method == "POST" else g.user)
