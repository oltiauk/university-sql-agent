INSERT INTO teachers (id, name) VALUES (1, 'Lira Hoxha'), (2, 'Besnik Krasniqi'), (3, 'Mira Berisha');

INSERT INTO students (id, name) VALUES
    (1, 'Arta'), (2, 'Blerim'), (3, 'Dren'), (4, 'Era'),
    (5, 'Fatos'), (6, 'Gent'), (7, 'Hana'), (8, 'Ilir');

INSERT INTO courses (id, code, title) VALUES
    (1, 'CS101', 'Databases'), (2, 'CS102', 'Algorithms'),
    (3, 'MA101', 'Statistics'), (4, 'CS201', 'Machine Learning');

INSERT INTO offerings (id, course_id, teacher_id, semester) VALUES
    (1, 1, 1, '2025-Fall'),
    (2, 2, 2, '2025-Fall'),
    (3, 3, 3, '2025-Fall'),
    (4, 1, 1, '2026-Spring'),
    (5, 4, 2, '2026-Spring'),
    (6, 3, 3, '2026-Spring');

INSERT INTO enrollments (student_id, offering_id, grade) VALUES
    (1, 1, 95), (2, 1, 88), (3, 1, 82), (4, 1, 70), (5, 1, 55),
    (1, 2, 98), (6, 2, 92), (7, 2, 90), (8, 2, 94),
    (2, 3, 91), (6, 3, 80), (7, 3, 96),
    (6, 4, 93), (7, 4, 97), (8, 4, 84),
    (1, 5, 99), (3, 5, 75),
    (4, 6, NULL), (5, 6, NULL);
