CREATE TABLE books (
    id     INTEGER PRIMARY KEY,
    title  TEXT NOT NULL,
    author TEXT NOT NULL
);

CREATE TABLE members (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE loans (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    member_id   INTEGER NOT NULL REFERENCES members(id),
    loaned_on   TEXT NOT NULL,   -- date, YYYY-MM-DD
    returned_on TEXT             -- date, NULL while the book is still out
);
