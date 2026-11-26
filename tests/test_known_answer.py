from tests.fakes import FakeModel
from tests.known_answer import check_answer, check_labelled, check_rows
from tests.test_graph import ABOVE_AVG
from tests.test_trace import traced

N = {"student": ("name", "student")}
GA = {"grade": ("grade",), "average": ("avg", "average", "mean")}

def test_check_rows_counts():
    assert check_rows(["name"], [("Arta",)], [{"student": "Arta"}, {"student": "Blerim"}, {"student": "Dren"}], N) == "expected 3 rows, got 1"

def test_check_rows_order_and_extra_columns_ignored():
    rows = [("Dren", 82.0, 1), ("Arta", 95.0, 1), ("Blerim", 88.0, 1)]
    assert check_rows(["student_name", "grade", "x"], rows, [{"student": "Arta"}, {"student": "Blerim"}, {"student": "Dren"}], N) is None

def test_check_rows_by_field():
    expected = [{"grade": 95, "average": 78.0}]
    assert check_rows(["grade", "class_average"], [(95.0, 78.0)], expected, GA) is None
    assert check_rows(["arta_grade", "average_grade"], [(95.0, 78.0)], expected, GA) is None
    assert check_rows(["class_average", "grade"], [(78.0, 95.0)], expected, GA) is None        # column order is free
    assert check_rows(["grade", "class_average"], [(78.0, 95.0)], expected, GA) is not None    # values swapped
    assert check_rows(["a", "b"], [(95.0, 78.0)], expected, GA) is not None                    # names do not fit

def test_check_rows_each_result_row_used_once():
    expected = [{"student": "Arta"}, {"student": "Arta"}]
    assert check_rows(["name"], [("Arta",), ("Blerim",)], expected, N) is not None

def test_check_rows_rounding_and_any_column():
    assert check_rows(["n"], [(91.3333,)], [{"value": 91.3}], {"value": ("",)}) is None

def test_check_answer_names():
    assert check_answer("Arta, Blerim and Dren scored above average.", ["Arta", "Blerim", "Dren"], ["Era", "Fatos"]) is None
    assert check_answer("Arta and Blerim scored above average.", ["Arta", "Blerim", "Dren"]) == "answer does not mention Dren"
    assert check_answer("Arta, Blerim, Dren and Era.", ["Arta"], ["Era"]) == "answer mentions Era"

def test_check_answer_numbers():
    assert check_answer("The average is 78.0.", [78]) is None
    assert check_answer("The average is 78.", [78.0]) is None
    assert check_answer("There are 178 rows.", [78]) is not None
    assert check_answer("It is 78.5.", [78]) is not None
    assert check_answer("There are five.", [5]) is not None

def test_check_labelled():
    labels = {"grade": 95, "class average": 78}
    assert check_labelled("Grade: 95. Class average: 78.0.", labels) is None
    assert check_labelled("**Grade:** 95\n**Class average:** 78", labels) is None
    assert check_labelled("Grade: 78. Class average: 95.", labels) == "answer does not show 95 after 'grade'"
    assert check_labelled("Upgrade: 95. Class average: 78.", labels) is not None
    assert check_labelled("Arta got 95 and the average was 78.", labels) is not None

# Demonstrations: a scripted wrong run, and the check that catches it.
EXPECTED = [{"student": "Arta"}, {"student": "Blerim"}, {"student": "Dren"}]

def test_wrong_sql_is_caught_and_traceable(db_path, tmp_path):
    wrong = ABOVE_AVG.split("AND e.grade >")[0] + "ORDER BY e.grade DESC LIMIT 1"
    rec = traced(FakeModel([{"sql": wrong}, "Arta scored above the class average."]), db_path, tmp_path)
    out = rec["steps"][2]["output"]
    assert check_rows(out["columns"], out["rows"], EXPECTED, N) == "expected 3 rows, got 1"
    assert "LIMIT 1" in rec["steps"][1]["output"]["sql"]          # the trace shows where it went wrong

def test_wrong_answer_is_caught(db_path, tmp_path):
    rec = traced(FakeModel([{"sql": ABOVE_AVG}, "Arta and Blerim scored above the class average."]), db_path, tmp_path)
    out = rec["steps"][2]["output"]
    assert check_rows(out["columns"], out["rows"], EXPECTED, N) is None       # the rows are right
    assert check_answer(rec["answer"], ["Arta", "Blerim", "Dren"], ["Era", "Fatos"]) == "answer does not mention Dren"

def test_swapped_answer_is_caught(db_path, tmp_path):
    sql = """SELECT e.grade AS grade, (SELECT AVG(e2.grade) FROM enrollments e2 WHERE e2.offering_id = e.offering_id) AS class_average
             FROM enrollments e WHERE e.student_id = 1 AND e.offering_id = 1"""
    rec = traced(FakeModel([{"sql": sql}, "Grade: 78. Class average: 95."]), db_path, tmp_path)
    out = rec["steps"][2]["output"]
    assert check_rows(out["columns"], out["rows"], [{"grade": 95, "average": 78.0}], GA) is None
    assert check_labelled(rec["answer"], {"grade": 95, "class average": 78}) == "answer does not show 95 after 'grade'"

def test_live_helper_uses_the_last_successful_execute(db_path, tmp_path):
    from tests.test_live import rows_problem   # here, as test_live imports N and EXPECTED from this module
    ok = traced(FakeModel([{"sql": "SELECT nope FROM students"}, {"sql": ABOVE_AVG}, "Arta, Blerim and Dren."]), db_path, tmp_path)
    assert rows_problem(ok, EXPECTED, N) is None
    bad = traced(FakeModel([{"sql": "SELECT nope FROM students"}, {"sql": "SELECT nope FROM students"}]), db_path, tmp_path)
    assert rows_problem(bad, EXPECTED, N) == "no successful execute_sql step"
