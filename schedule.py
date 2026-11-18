from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import abort

from common import FormError, date_value, field, number, score_value, stamp
from db import row, rows, write_transaction

EXAM_SELECT = """
    SELECT e.*, c.code AS course_code, c.name AS course_name, c.active AS course_active
    FROM exams e JOIN courses c ON c.id = e.course_id
"""

SLOT_SELECT = """
    SELECT s.*, e.title AS exam_title, u.name AS examiner_name, u.status AS examiner_status,
           s.capacity - (SELECT COUNT(*) FROM bookings b
                         WHERE b.slot_id = s.id AND b.status IN ('booked', 'completed')) AS seats_left
    FROM slots s JOIN exams e ON e.id = s.exam_id JOIN users u ON u.id = s.examiner_id
"""

BOOKING_SELECT = """
    SELECT b.*, s.start_at, s.end_at, s.examiner_id, u.name AS student_name, u.roll_no,
           x.name AS examiner_name, e.title AS exam_title, e.max_marks, e.booking_close,
           v.total, v.remarks, v.marked_at, marker.name AS marked_by
    FROM bookings b JOIN slots s ON s.id = b.slot_id JOIN users u ON u.id = b.student_id
    JOIN users x ON x.id = s.examiner_id JOIN exams e ON e.id = b.exam_id
    LEFT JOIN evaluations v ON v.booking_id = b.id LEFT JOIN users marker ON marker.id = v.examiner_id
"""

EXAM_STATUSES = ("draft", "slot_creation", "booking_open", "closed", "completed")


def exam_by_id(exam_id):
    exam = row(EXAM_SELECT + " WHERE e.id = ?", (exam_id,))
    if exam is None:
        abort(404)
    return exam


def save_exam(form, exam_id=None):
    course_id = number(form, "course_id", "Course", maximum=2**63 - 1)
    title = field(form, "title", "Examination title")
    duration = number(form, "duration", "Duration", 1, 720)
    max_marks = number(form, "max_marks", "Maximum marks")
    slot_open = stamp(date_value(form.get("slot_open", ""), "Slot creation opens"))
    slot_close = stamp(date_value(form.get("slot_close", ""), "Slot creation closes"))
    booking_start = stamp(date_value(form.get("booking_open", ""), "Booking opens"))
    booking_end = stamp(date_value(form.get("booking_close", ""), "Booking closes"))

    if not slot_open < slot_close <= booking_start < booking_end:
        raise FormError(
            "Slot creation must end before or when booking opens. Each window needs an end after its start."
        )

    values = (course_id, title, duration, max_marks, slot_open, slot_close, booking_start, booking_end)
    with write_transaction() as db:
        course = row("SELECT active FROM courses WHERE id = ?", (course_id,))
        current = exam_by_id(exam_id) if exam_id is not None else None
        keeps_course = current is not None and current["course_id"] == course_id
        if course is None or (not course["active"] and not keeps_course):
            raise FormError("Choose an active course.")

        if current is None:
            return db.execute(
                """
                INSERT INTO exams (course_id, title, duration, max_marks, slot_open, slot_close,
                                   booking_open, booking_close, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')
                """,
                values,
            ).lastrowid

        check_exam_changes(current, course_id, duration, max_marks, booking_start, booking_end)
        db.execute(
            """
            UPDATE exams SET course_id = ?, title = ?, duration = ?, max_marks = ?,
                             slot_open = ?, slot_close = ?, booking_open = ?, booking_close = ?
            WHERE id = ?
            """,
            (*values, exam_id),
        )
        return exam_id


def check_exam_changes(exam, course_id, duration, max_marks, booking_start, booking_end):
    exam_id = exam["id"]
    if exam["status"] == "completed":
        raise FormError("Completed examinations are kept for reference.")

    changes_slot_setup = exam["course_id"] != course_id or exam["duration"] != duration
    if changes_slot_setup and row("SELECT 1 FROM slots WHERE exam_id = ? LIMIT 1", (exam_id,)):
        raise FormError("Course and duration are fixed once a slot exists.")

    first_slot = row(
        "SELECT MIN(start_at) AS start_at FROM slots WHERE exam_id = ? AND status = 'open'",
        (exam_id,),
    )["start_at"]
    if first_slot is not None and booking_end > first_slot:
        raise FormError("Booking must close by the start of the earliest slot.")

    if exam["booking_open"] != booking_start and row(
        "SELECT 1 FROM bookings WHERE exam_id = ? LIMIT 1", (exam_id,)
    ):
        raise FormError("Booking start is fixed once there is booking history.")
    if exam["max_marks"] != max_marks and row(
        "SELECT 1 FROM bookings WHERE exam_id = ? AND status = 'completed' LIMIT 1", (exam_id,)
    ):
        raise FormError("Maximum marks are fixed once a result exists.")


def booking_open(exam, time):
    return (
        exam["course_active"]
        and exam["status"] == "booking_open"
        and exam["booking_open"] <= time < exam["booking_close"]
    )


def cancelling_allowed(booking, time):
    deadline = min(booking["booking_close"], booking["start_at"])
    return booking["status"] == "booked" and time < deadline


def slot_creation_open(exam, time):
    return (
        exam["course_active"]
        and exam["status"] == "slot_creation"
        and exam["slot_open"] <= time < exam["slot_close"]
    )


def slot_editing_allowed(exam, time):
    return (
        exam["course_active"]
        and exam["status"] in ("draft", "slot_creation", "closed")
        and time < exam["booking_open"]
    )


def slot_by_id(slot_id):
    slot = row(SLOT_SELECT + " WHERE s.id = ?", (slot_id,))
    if slot is None:
        abort(404)
    return slot


def assigned_exam(exam_id, examiner_id):
    exam = exam_by_id(exam_id)
    if not row("SELECT 1 FROM exam_examiners WHERE exam_id = ? AND examiner_id = ?", (exam_id, examiner_id)):
        abort(404)
    return exam


def rubric_for(exam_id):
    return rows("SELECT * FROM rubrics WHERE exam_id = ? ORDER BY id", (exam_id,))


def rubric_is_ready(rubrics):
    return sum(criterion["weight"] for criterion in rubrics) == 100


def check_overlap(examiner_id, start, end, excluded_id=0):
    clash = row(
        """SELECT id FROM slots WHERE examiner_id = ? AND status = 'open' AND id != ?
           AND start_at < ? AND end_at > ?""",
        (examiner_id, excluded_id, end, start),
    )
    if clash:
        raise FormError("This time overlaps another slot for the examiner.")


def save_slot(exam_id, examiner_id, form, slot_id=None):
    with write_transaction() as db:
        exam = assigned_exam(exam_id, examiner_id)
        time = stamp()
        if slot_id is None:
            if not slot_creation_open(exam, time):
                raise FormError("Slot creation is closed for this examination.")
        else:
            slot = slot_by_id(slot_id)
            if slot["exam_id"] != exam_id or slot["examiner_id"] != examiner_id:
                abort(404)
            if slot["status"] != "open" or not slot_editing_allowed(exam, time):
                raise FormError("This slot can only be changed before booking starts.")
        start = date_value(form.get("start_at", ""), "Slot start")
        start_at = stamp(start)
        end_at = stamp(start + timedelta(minutes=exam["duration"]))
        if start_at < exam["booking_close"]:
            raise FormError("The slot must start at or after the booking deadline.")
        capacity = number(form, "capacity", "Capacity", 1, 1000)
        check_overlap(examiner_id, start_at, end_at, slot_id or 0)
        if slot_id is None:
            db.execute(
                "INSERT INTO slots (exam_id, examiner_id, start_at, end_at, capacity) VALUES (?, ?, ?, ?, ?)",
                (exam_id, examiner_id, start_at, end_at, capacity),
            )
            return

        seats_taken = slot["capacity"] - slot["seats_left"]
        if capacity < seats_taken:
            raise FormError("Capacity cannot be below the number already booked.")
        db.execute(
            "UPDATE slots SET start_at = ?, end_at = ?, capacity = ? WHERE id = ?",
            (start_at, end_at, capacity, slot_id),
        )


def remove_slot(slot_id, examiner_id):
    with write_transaction() as db:
        slot = slot_by_id(slot_id)
        if slot["examiner_id"] != examiner_id:
            abort(404)
        exam = exam_by_id(slot["exam_id"])
        if slot["status"] != "open" or not slot_editing_allowed(exam, stamp()):
            raise FormError("This slot can only be removed before booking starts.")
        if slot["seats_left"] != slot["capacity"]:
            raise FormError("Move existing bookings before removing this slot.")
        db.execute("UPDATE slots SET status = 'removed' WHERE id = ?", (slot_id,))


def check_slot_available(slot, time):
    if slot["status"] != "open" or slot["examiner_status"] != "active" or slot["start_at"] <= time:
        raise FormError("This slot is no longer available.")
    if slot["seats_left"] <= 0:
        raise FormError("This slot is full.")


def live_booking(student_id, exam_id):
    return row(
        """
        SELECT id FROM bookings
        WHERE student_id = ? AND exam_id = ? AND status IN ('booked', 'completed')
        """,
        (student_id, exam_id),
    )


def book_slot(student_id, slot_id):
    with write_transaction() as db:
        time = stamp()
        slot = slot_by_id(slot_id)
        exam = exam_by_id(slot["exam_id"])
        if not booking_open(exam, time):
            raise FormError("Booking is closed for this examination.")
        if live_booking(student_id, exam["id"]):
            raise FormError("You already have a booking for this examination.")
        check_slot_available(slot, time)
        db.execute("INSERT INTO bookings (student_id, exam_id, slot_id, booked_at) VALUES (?, ?, ?, ?)",
                   (student_id, exam["id"], slot_id, time))
        return exam["id"]


def booking_by_id(booking_id):
    booking = row(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,))
    if booking is None:
        abort(404)
    return booking


def cancel_booking(booking_id, student_id):
    with write_transaction() as db:
        booking = booking_by_id(booking_id)
        if booking["student_id"] != student_id:
            abort(404)
        if booking["status"] != "booked":
            raise FormError("Only current bookings can be cancelled.")
        if not cancelling_allowed(booking, stamp()):
            raise FormError("The cancellation deadline has passed.")
        db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))


def reschedule_booking(booking_id, slot_id):
    with write_transaction() as db:
        time = stamp()
        original = booking_by_id(booking_id)
        target = slot_by_id(slot_id)
        if original["status"] != "booked":
            raise FormError("Only current bookings can be rescheduled.")
        if target["exam_id"] != original["exam_id"] or slot_id == original["slot_id"]:
            raise FormError("Choose another slot for the same examination.")
        check_slot_available(target, time)

        # Keep the old slot in the history and free its seat before creating the new booking.
        db.execute("UPDATE bookings SET status = 'rescheduled' WHERE id = ?", (booking_id,))
        db.execute(
            """INSERT INTO bookings (student_id, exam_id, slot_id, booked_at, moved_from_id)
               VALUES (?, ?, ?, ?, ?)""",
            (original["student_id"], original["exam_id"], slot_id, time, booking_id),
        )


def replace_examiner(slot_id, examiner_id):
    with write_transaction() as db:
        slot = slot_by_id(slot_id)
        if slot["status"] != "open" or row("SELECT 1 FROM bookings WHERE slot_id = ? AND status = 'completed'", (slot_id,)):
            raise FormError("Only open slots without completed evaluations can be reassigned.")
        if not row("SELECT 1 FROM users WHERE id = ? AND role = 'examiner' AND status = 'active'", (examiner_id,)):
            raise FormError("Choose an active examiner.")
        check_overlap(examiner_id, slot["start_at"], slot["end_at"], slot_id)
        db.execute("INSERT OR IGNORE INTO exam_examiners (exam_id, examiner_id) VALUES (?, ?)",
                   (slot["exam_id"], examiner_id))
        db.execute("UPDATE slots SET examiner_id = ? WHERE id = ?", (examiner_id, slot_id))


def scores_for(booking_id):
    return rows(
        """SELECT r.criterion, r.max_marks, r.weight, s.marks FROM evaluation_scores s
           JOIN rubrics r ON r.id = s.rubric_id WHERE s.booking_id = ? ORDER BY r.id""", (booking_id,)
    )


def evaluate(booking_id, examiner_id, form):
    with write_transaction() as db:
        booking = booking_by_id(booking_id)
        if booking["examiner_id"] != examiner_id:
            abort(404)
        if booking["status"] != "booked":
            raise FormError("Only current, unmarked bookings can be evaluated.")
        marked_at = stamp()
        if marked_at < booking["end_at"]:
            raise FormError("You can submit marks after the slot ends.")
        rubrics = rubric_for(booking["exam_id"])
        if not rubric_is_ready(rubrics):
            raise FormError("Ask the admin to set up a rubric with weights that total 100%.")
        scores = []
        weighted_percent = Decimal(0)
        for criterion in rubrics:
            marks = score_value(form, criterion)
            scores.append((booking_id, criterion["id"], float(marks)))
            weighted_percent += marks * criterion["weight"] / criterion["max_marks"]

        remarks = field(form, "remarks", "Remarks", 2000, required=False)
        total = weighted_percent * booking["max_marks"] / 100
        total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        db.execute(
            """
            INSERT INTO evaluations (booking_id, examiner_id, total, remarks, marked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (booking_id, examiner_id, float(total), remarks, marked_at),
        )
        db.executemany(
            "INSERT INTO evaluation_scores (booking_id, rubric_id, marks) VALUES (?, ?, ?)", scores
        )
        db.execute("UPDATE bookings SET status = 'completed' WHERE id = ?", (booking_id,))
