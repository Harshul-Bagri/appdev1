# Examination Management Portal

The portal lets admins arrange examinations, examiners set up slots and mark students, and students book an exam and see the outcome. It runs locally with Flask, Jinja, Bootstrap CSS, and SQLite.

## Run on Windows

Open PowerShell in this folder. Python 3.10 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app seed-demo
.\.venv\Scripts\python.exe app.py
```

Open [the portal](http://127.0.0.1:5000). On macOS or Linux, use `.venv/bin/python` in place of the Windows virtual environment executable. On first launch, the app creates `instance/portal.sqlite` and the admin account. Skip `seed-demo` to start with an empty portal and the same default admin.

## Demo accounts

All demo accounts use `DemoPass123`.

| Role | Email |
| --- | --- |
| Admin | admin@example.test |
| Examiner | examiner@example.test |
| Second examiner | examiner2@example.test |
| Examiner awaiting approval | pending@example.test |
| Student | student@example.test |
| Second student | student2@example.test |

1. As admin, open **People** to find the pending examiner. Open **Examinations** to see the timeline and rubric.
2. As examiner, open **Database Practical** and create a slot with a start time on or after its booking deadline.
3. As a student, open **Application Demo Viva** and book a seat. The first slot has capacity one. A second student can see it fill up.
4. As admin, open that booking and move it to the second slot. The original will show **Rescheduled** in the student's history.
5. As examiner, open the roster for the seeded past slot under **Web Programming Practical**. Mark the ungraded booking. The same student can sign in and open **My bookings** to see the result.

Seeded dates are relative to the day the command runs, and all times use IST. To reset demo dates, stop the server and move `instance/portal.sqlite` out of the folder before running `seed-demo` again. Moving that file also moves any changes made to that database. To choose different initial admin credentials for a clean database, set `PORTAL_ADMIN_EMAIL` and `PORTAL_ADMIN_PASSWORD` in the environment before the first run. The included credentials are intended for the local demo.

## Code and checks

- `app.py` creates the app and checks form tokens. `auth.py` handles sign-in and registration.
- `admin.py`, `examiner.py` and `student.py` handle requests for each role. `schedule.py` contains the rules shared by these pages.
- `schema.sql` defines the SQLite tables. `db.py` handles connections and write transactions. `demo.py` sets up the initial account and sample records.
- `templates/` contains the Jinja pages and `static/` contains local CSS. The core workflows use ordinary HTML form submissions.
- `api.py` provides read-only JSON endpoints. Sign in through the browser, then open `/api/exams` or `/api/bookings` in the same session. Admins can also open `/api/examiners` and `/api/students`.

Run the tests from this folder.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Report and API

The project report is in Examination Management Portal Report.pdf. The API definition is in api.yaml.
