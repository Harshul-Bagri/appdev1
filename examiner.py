from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import check_role
from common import FormError, stamp
from db import rows
from schedule import (
    BOOKING_SELECT, EXAM_SELECT, SLOT_SELECT, assigned_exam, booking_by_id, evaluate, remove_slot,
    rubric_for, rubric_is_ready, save_slot, scores_for, slot_by_id, slot_creation_open, slot_editing_allowed,
)

bp = Blueprint("examiner", __name__, url_prefix="/examiner")


@bp.before_request
def check_examiner():
    return check_role("examiner")


@bp.get("/")
def dashboard():
    examiner_id = g.user["id"]
    exams = rows(EXAM_SELECT + """
        JOIN exam_examiners a ON a.exam_id = e.id WHERE a.examiner_id = ? ORDER BY e.booking_open DESC
    """, (examiner_id,))
    slots = rows(SLOT_SELECT + " WHERE s.examiner_id = ? ORDER BY s.start_at DESC", (examiner_id,))
    return render_template("examiner/dashboard.html", exams=exams, slots=slots)


@bp.get("/exams/<int:exam_id>")
def exam_detail(exam_id):
    exam = assigned_exam(exam_id, g.user["id"])
    slots = rows(SLOT_SELECT + " WHERE s.examiner_id = ? AND s.exam_id = ? ORDER BY s.start_at",
                 (g.user["id"], exam_id))
    return render_template("examiner/exam_detail.html", exam=exam, slots=slots, rubrics=rubric_for(exam_id),
                           can_create=slot_creation_open(exam, stamp()))


def own_slot(slot_id):
    slot = slot_by_id(slot_id)
    if slot["examiner_id"] != g.user["id"]:
        abort(404)
    return slot


@bp.route("/exams/<int:exam_id>/slots/new", methods=["GET", "POST"])
@bp.route("/slots/<int:slot_id>/edit", methods=["GET", "POST"])
def slot_form(exam_id=None, slot_id=None):
    examiner_id = g.user["id"]
    if slot_id is not None:
        slot = own_slot(slot_id)
        exam_id = slot["exam_id"]
    else:
        slot = {}

    exam = assigned_exam(exam_id, examiner_id)
    time = stamp()
    if slot:
        can_change = slot["status"] == "open" and slot_editing_allowed(exam, time)
    else:
        can_change = slot_creation_open(exam, time)

    if request.method == "POST":
        try:
            save_slot(exam_id, examiner_id, request.form, slot_id)
        except FormError as error:
            flash(str(error), "error")
        else:
            flash("Slot updated." if slot_id else "Slot created.")
            return redirect(url_for("examiner.exam_detail", exam_id=exam_id))
    return render_template("examiner/slot_form.html", exam=exam, slot=slot, can_change=can_change,
                           values=request.form if request.method == "POST" else slot)


@bp.post("/slots/<int:slot_id>/remove")
def slot_remove(slot_id):
    slot = own_slot(slot_id)
    try:
        remove_slot(slot_id, g.user["id"])
    except FormError as error:
        flash(str(error), "error")
    else:
        flash("Slot removed.")
    return redirect(url_for("examiner.exam_detail", exam_id=slot["exam_id"]))


@bp.get("/slots/<int:slot_id>")
def slot_detail(slot_id):
    slot = own_slot(slot_id)
    exam = assigned_exam(slot["exam_id"], g.user["id"])
    roster = rows(BOOKING_SELECT + " WHERE b.slot_id = ? ORDER BY u.name, b.id DESC", (slot_id,))
    can_change = slot["status"] == "open" and slot_editing_allowed(exam, stamp())
    return render_template("examiner/slot_detail.html", slot=slot, bookings=roster, can_change=can_change)


@bp.route("/bookings/<int:booking_id>/evaluate", methods=["GET", "POST"])
def evaluate_booking(booking_id):
    booking = booking_by_id(booking_id)
    if booking["examiner_id"] != g.user["id"]:
        abort(404)
    if request.method == "POST":
        try:
            evaluate(booking_id, g.user["id"], request.form)
        except FormError as error:
            flash(str(error), "error")
        else:
            flash("Evaluation saved.")
            return redirect(url_for("examiner.evaluate_booking", booking_id=booking_id))
    if booking["status"] != "booked":
        return render_template("booking_detail.html", booking=booking, scores=scores_for(booking_id))

    rubrics = rubric_for(booking["exam_id"])
    slot_has_ended = stamp() >= booking["end_at"]
    return render_template(
        "examiner/evaluate.html",
        booking=booking,
        rubrics=rubrics,
        can_mark=slot_has_ended and rubric_is_ready(rubrics),
        ended=slot_has_ended,
    )
