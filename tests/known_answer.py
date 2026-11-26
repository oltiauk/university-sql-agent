"""Test-only checks of a run against known correct values. Each returns None on success, else a one-line message."""
import re
from collections import Counter
from itertools import permutations


def _norm(v):
    return round(v, 1) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def check_rows(columns, rows, expected, names):
    if len(rows) != len(expected):
        return f"expected {len(expected)} rows, got {len(rows)}"
    fields = list(names)
    want = Counter(tuple(_norm(e[f]) for f in fields) for e in expected)
    fits = [[i for i, c in enumerate(columns) if any(w in c.lower() for w in names[f])] for f in fields]
    for pick in permutations(range(len(columns)), len(fields)):
        if all(p in fit for p, fit in zip(pick, fits)) \
                and Counter(tuple(_norm(r[p]) for p in pick) for r in rows) == want:
            return None
    return "rows do not match the expected values by field"


def _number(n):
    # own number: no digit (or "digit.") before, no digit after; ".0"/".00" allowed, ".5" is not
    return re.compile(r"(?<!\d)(?<!\d\.)" + re.escape(f"{n:g}") + r"(?:\.0+)?(?!\d)(?!\.\d)")


def _appears(answer, value):
    if isinstance(value, (int, float)):
        return _number(value).search(answer) is not None
    return value in answer


def check_answer(answer, must, must_not=()):
    for v in must:
        if not _appears(answer, v):
            return f"answer does not mention {v}"
    for v in must_not:
        if _appears(answer, v):
            return f"answer mentions {v}"


def check_labelled(answer, labels):
    for label, n in labels.items():
        if not re.search(re.escape(label.lower()) + r"[^a-zA-Z0-9]*" + _number(n).pattern, answer.lower()):
            return f"answer does not show {n} after '{label}'"
