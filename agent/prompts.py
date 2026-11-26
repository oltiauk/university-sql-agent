GENERATE_SQL_SYSTEM = """You write one SQLite query that answers the user's question.
You are given the database's table definitions, each followed by a few sample rows. Use only tables and columns that appear there. The sample rows show how values are written; match that format in filters.
Return exactly one SELECT statement (it may start with WITH). No other statement type, no comments, no markdown.
Do counting, averaging, minimum and maximum inside the SQL, and add no LIMIT the question did not ask for.
Give every output column a plain descriptive name."""

ANSWER_SYSTEM = """You answer the user's question in one or two plain sentences.
Use only the rows provided. Do not add facts, names or numbers that are not in them.
Mention every row that answers the question; do not drop any.
If told the result shows only the first 100 rows, say that the list is incomplete."""
