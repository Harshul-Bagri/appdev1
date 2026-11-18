import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier

from app import create_app
from db import get_db


class PortalTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = datetime(2026, 11, 18, 10)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DATABASE": Path(directory.name) / "test.sqlite",
            "NOW": lambda: self.now,
        })
        self.assertEqual(self.app.test_cli_runner().invoke(args=["seed-demo"]).exit_code, 0)
        self.admin = self.login("admin")
        self.examiner = self.login("examiner")
        self.student = self.login("student")
        self.other = self.login("student2")

    def post(self, client, path, **data):
        csrf = client.get("/", follow_redirects=True)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', csrf.data)[1].decode()
        return client.post(path, data={"csrf_token": token, **data}, follow_redirects=True)

    def login(self, account):
        client = self.app.test_client()
        response = self.post(client, "/login", email=f"{account}@example.test", password="DemoPass123")
        self.assertIn(b"Sign out", response.data)
        return client

    def test_role_checks_and_pending_account(self):
        self.assertEqual(self.student.get("/admin/").status_code, 403)
        self.assertEqual(self.examiner.get("/student/").status_code, 403)
        self.assertEqual(self.app.test_client().get("/api/exams").status_code, 401)
        client = self.app.test_client()
        self.post(client, "/register/examiner", name="New Examiner", email="new@example.test",
                  department="Computing", password="NewPass123")
        self.assertIn(b"pending approval", self.post(client, "/login", email="new@example.test",
                                                   password="NewPass123").data)
        pending = self.admin.get("/admin/people?role=examiner&q=New+Examiner").data
        self.assertIn(b"New Examiner", pending)
        self.assertIn(b">Approve</button>", pending)
        with self.app.app_context():
            user = get_db().execute("SELECT id FROM users WHERE email='new@example.test'").fetchone()
        self.post(self.admin, f"/admin/people/{user['id']}/status", status="active")
        self.assertIn(b"Sign out", self.post(client, "/login", email="new@example.test",
                                            password="NewPass123").data)
        self.assertEqual(self.student.post("/student/slots/1/book").status_code, 400)

    def test_booking_capacity_duplicate_and_cancellation(self):
        self.assertIn(b"Booking confirmed", self.post(self.student, "/student/slots/1/book").data)
        self.assertIn(b"already have a booking", self.post(self.student, "/student/slots/2/book").data)
        self.assertIn(b"slot is full", self.post(self.other, "/student/slots/1/book").data)
        rows = self.student.get("/api/bookings").json
        booking_id = next(row["id"] for row in rows if row["slot_id"] == 1)
        self.assertEqual(self.other.get(f"/student/bookings/{booking_id}").status_code, 404)
        self.assertIn(b"Booking cancelled", self.post(self.student, f"/student/bookings/{booking_id}/cancel").data)
        self.assertIn(b"Booking confirmed", self.post(self.other, "/student/slots/1/book").data)

    def test_reschedule_and_mark(self):
        self.post(self.student, "/student/slots/1/book")
        old = next(row for row in self.student.get("/api/bookings").json if row["slot_id"] == 1)
        self.assertIn(b"Booking rescheduled", self.post(self.admin, f"/admin/bookings/{old['id']}",
                                                      slot_id=2).data)
        bookings = self.student.get("/api/bookings").json
        moved = next(row for row in bookings if row["slot_id"] == 2)
        self.assertEqual(moved["moved_from_id"], old["id"])
        mark_url = f"/examiner/bookings/{moved['id']}/evaluate"
        self.assertIn(b"after the slot ends", self.post(self.examiner, mark_url).data)
        self.now += timedelta(days=4)
        self.assertIn(b"between 0 and", self.post(self.examiner, mark_url, score_1=11, score_2=8).data)
        self.assertIn(b"Evaluation saved", self.post(self.examiner, mark_url, score_1=8, score_2=7,
                                                    remarks="Clear demonstration.").data)
        result = self.student.get(f"/student/bookings/{moved['id']}")
        self.assertIn(b"15 / 20", result.data)
        self.assertIn(b"Clear demonstration.", result.data)

    def test_dates_and_slot_overlap(self):
        self.assertIn(b"Slot creation is closed", self.post(self.examiner, "/examiner/exams/1/slots/new").data)
        self.assertIn(b"Slot created", self.post(self.examiner, "/examiner/exams/2/slots/new",
                                                start_at="2026-11-26T10:00", capacity=2).data)
        self.assertIn(b"overlaps", self.post(self.examiner, "/examiner/exams/2/slots/new",
                                            start_at="2026-11-26T10:15", capacity=2).data)
        self.post(self.student, "/student/slots/1/book")
        booking_id = next(row["id"] for row in self.student.get("/api/bookings").json if row["slot_id"] == 1)
        self.now += timedelta(days=3)
        self.assertIn(b"cancellation deadline has passed",
                      self.post(self.student, f"/student/bookings/{booking_id}/cancel").data)

    def test_two_students_race_for_the_last_seat(self):
        barrier = Barrier(2)

        def book_as(client):
            with client.session_transaction() as session:
                token = session["csrf_token"]
            barrier.wait(timeout=5)
            result = client.post("/student/slots/1/book", data={"csrf_token": token}, follow_redirects=True)
            return b"Booking confirmed" in result.data, b"slot is full" in result.data

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(book_as, (self.student, self.other)))
        self.assertCountEqual(results, [(True, False), (False, True)])

    def test_admin_setup_and_examiner_reassignment(self):
        self.assertIn(b"Course saved", self.post(self.admin, "/admin/courses/new", code="CS102",
                                                name="Programming Lab", active=1).data)
        with self.app.app_context():
            course_id = get_db().execute("SELECT id FROM courses WHERE code='CS102'").fetchone()["id"]
        result = self.post(self.admin, "/admin/exams/new", title="Programming Viva", course_id=course_id,
                           duration=25, max_marks=50, slot_open="2026-11-17T10:00",
                           slot_close="2026-11-19T10:00", booking_open="2026-11-20T10:00",
                           booking_close="2026-11-21T10:00")
        self.assertIn(b"Examination saved", result.data)
        exam = next(item for item in self.admin.get("/api/exams").json if item["title"] == "Programming Viva")
        self.assertIn(b"Set rubric weights", self.post(self.admin, f"/admin/exams/{exam['id']}/status",
                                                      status="booking_open").data)
        self.assertIn(b"Rubric updated", self.post(self.admin, f"/admin/exams/{exam['id']}/rubrics/new",
                                                   criterion="Program", max_marks=50, weight=100).data)
        self.assertIn(b"Course archived", self.post(self.admin, f"/admin/courses/{course_id}/remove").data)
        second_examiner = next(person for person in self.admin.get("/api/examiners").json if person["name"] == "Dr Arun Sen")
        self.assertIn(b"Slot examiner updated", self.post(self.admin, "/admin/slots/1",
                                                          examiner_id=second_examiner["id"]).data)
        self.assertEqual(self.examiner.get("/examiner/slots/1").status_code, 404)
        self.assertEqual(self.login("examiner2").get("/examiner/slots/1").status_code, 200)

    def test_filtered_lists_and_profile(self):
        self.assertIn(b"Application Demo Viva", self.student.get("/student/?q=Viva&course=1").data)
        self.assertNotIn(b"Database Practical", self.student.get("/student/?q=Viva&course=1").data)
        self.assertIn(b"No examinations found", self.student.get("/student/?q=%25").data)
        self.assertEqual(self.student.get("/api/students").status_code, 403)
        profile = self.post(self.student, "/student/profile", name="Riya Das", email="riya@example.test", roll_no="DEMO01")
        self.assertIn(b"Profile updated", profile.data)
        self.assertIn(b"riya@example.test", profile.data)


if __name__ == "__main__":
    unittest.main()
