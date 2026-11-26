CREATE TABLE teachers (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE students (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE courses (
    id    INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL
);

-- One course taught by one teacher in one semester, e.g. '2025-Fall'.
CREATE TABLE offerings (
    id         INTEGER PRIMARY KEY,
    course_id  INTEGER NOT NULL REFERENCES courses(id),
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    semester   TEXT NOT NULL,
    UNIQUE (course_id, teacher_id, semester)
);

-- A student in an offering. grade is 0 to 100, NULL until graded.
CREATE TABLE enrollments (
    student_id  INTEGER NOT NULL REFERENCES students(id),
    offering_id INTEGER NOT NULL REFERENCES offerings(id),
    grade       REAL CHECK (grade BETWEEN 0 AND 100),
    PRIMARY KEY (student_id, offering_id)
);
