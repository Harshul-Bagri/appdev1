CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'examiner', 'student')),
    roll_no TEXT COLLATE NOCASE UNIQUE,
    department TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'inactive'))
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    title TEXT NOT NULL,
    duration INTEGER NOT NULL CHECK (duration > 0),
    max_marks INTEGER NOT NULL CHECK (max_marks > 0),
    slot_open TEXT NOT NULL,
    slot_close TEXT NOT NULL,
    booking_open TEXT NOT NULL,
    booking_close TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'slot_creation', 'booking_open', 'closed', 'completed')),
    CHECK (slot_open < slot_close AND slot_close <= booking_open AND booking_open < booking_close)
);

CREATE TABLE IF NOT EXISTS exam_examiners (
    exam_id INTEGER NOT NULL REFERENCES exams(id),
    examiner_id INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (exam_id, examiner_id)
);

CREATE TABLE IF NOT EXISTS rubrics (
    id INTEGER PRIMARY KEY,
    exam_id INTEGER NOT NULL REFERENCES exams(id),
    criterion TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    max_marks INTEGER NOT NULL CHECK (max_marks > 0),
    weight INTEGER NOT NULL CHECK (weight BETWEEN 1 AND 100),
    UNIQUE (exam_id, criterion)
);

CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY,
    exam_id INTEGER NOT NULL,
    examiner_id INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'removed')),
    CHECK (start_at < end_at),
    UNIQUE (id, exam_id),
    FOREIGN KEY (exam_id, examiner_id) REFERENCES exam_examiners(exam_id, examiner_id)
);

CREATE INDEX IF NOT EXISTS slots_by_examiner ON slots(examiner_id, start_at);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id),
    exam_id INTEGER NOT NULL,
    slot_id INTEGER NOT NULL,
    booked_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked'
        CHECK (status IN ('booked', 'cancelled', 'rescheduled', 'completed')),
    moved_from_id INTEGER UNIQUE REFERENCES bookings(id),
    FOREIGN KEY (slot_id, exam_id) REFERENCES slots(id, exam_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_exam
    ON bookings(student_id, exam_id) WHERE status IN ('booked', 'completed');
CREATE INDEX IF NOT EXISTS bookings_by_slot ON bookings(slot_id, status);

CREATE TABLE IF NOT EXISTS evaluations (
    booking_id INTEGER PRIMARY KEY REFERENCES bookings(id),
    examiner_id INTEGER NOT NULL REFERENCES users(id),
    total REAL NOT NULL CHECK (total >= 0),
    remarks TEXT NOT NULL,
    marked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_scores (
    booking_id INTEGER NOT NULL REFERENCES evaluations(booking_id),
    rubric_id INTEGER NOT NULL REFERENCES rubrics(id),
    marks REAL NOT NULL CHECK (marks >= 0),
    PRIMARY KEY (booking_id, rubric_id)
);
