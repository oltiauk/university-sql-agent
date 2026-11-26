INSERT INTO books (id, title, author) VALUES
    (1, 'Dune', 'Frank Herbert'),
    (2, 'Emma', 'Jane Austen'),
    (3, 'Ulysses', 'James Joyce'),
    (4, 'Beloved', 'Toni Morrison'),
    (5, 'Dracula', 'Bram Stoker'),
    (6, 'Walden', 'Henry David Thoreau');

INSERT INTO members (id, name) VALUES
    (1, 'Agon'),
    (2, 'Besa'),
    (3, 'Drita'),
    (4, 'Luan');

INSERT INTO loans (id, book_id, member_id, loaned_on, returned_on) VALUES
    (1, 1, 1, '2026-01-05', '2026-01-20'),
    (2, 2, 2, '2026-02-01', NULL),
    (3, 3, 1, '2026-02-10', '2026-03-01'),
    (4, 1, 3, '2026-03-02', NULL),
    (5, 4, 4, '2026-03-05', '2026-03-19'),
    (6, 5, 2, '2026-03-10', NULL),
    (7, 2, 1, '2026-01-10', '2026-01-25'),
    (8, 6, 3, '2026-01-15', '2026-02-01');
