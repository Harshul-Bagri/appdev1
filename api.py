from flask import Blueprint, g, jsonify

from common import stamp
from db import rows

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.before_request
def signed_in():
    if not g.user:
        return jsonify(error="Sign in to use the API."), 401


@bp.get("/exams")
def exams():
    query = """
        SELECT e.id, e.title, c.code AS course_code, e.status, e.duration, e.max_marks,
               e.slot_open, e.slot_close, e.booking_open, e.booking_close
        FROM exams e JOIN courses c ON c.id = e.course_id
    """
    params = ()
    if g.user["role"] == "examiner":
        query += " JOIN exam_examiners a ON a.exam_id = e.id WHERE a.examiner_id = ?"
        params = (g.user["id"],)
    elif g.user["role"] == "student":
        query += """
            WHERE (c.active = 1 AND e.status IN ('slot_creation', 'booking_open', 'closed')
                   AND e.booking_close > ?)
               OR EXISTS (SELECT 1 FROM bookings b WHERE b.exam_id = e.id AND b.student_id = ?)
        """
        params = (stamp(), g.user["id"])
    return jsonify([dict(exam) for exam in rows(query + " ORDER BY e.id", params)])


@bp.get("/bookings")
def bookings():
    query = """
        SELECT b.id, b.exam_id, b.slot_id, b.student_id, b.status, b.booked_at, b.moved_from_id
        FROM bookings b JOIN slots s ON s.id = b.slot_id
    """
    params = ()
    if g.user["role"] == "student":
        query += " WHERE b.student_id = ?"
        params = (g.user["id"],)
    elif g.user["role"] == "examiner":
        query += " WHERE s.examiner_id = ?"
        params = (g.user["id"],)
    return jsonify([dict(booking) for booking in rows(query + " ORDER BY b.id", params)])


@bp.get("/examiners")
def examiners():
    return people("examiner")


@bp.get("/students")
def students():
    return people("student")


def people(role):
    if g.user["role"] != "admin":
        return jsonify(error="This list is available to admins only."), 403
    query = "SELECT id, name, email, department, roll_no, status FROM users WHERE role = ? ORDER BY name"
    return jsonify([dict(person) for person in rows(query, (role,))])
