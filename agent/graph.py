import sqlite3
from contextlib import closing
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agent.db import connect, get_schema, run_query
from agent.prompts import ANSWER_SYSTEM, GENERATE_SQL_SYSTEM


class State(TypedDict, total=False):
    question: str
    schema: str
    sql: str
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    error: str
    answer: str
    status: str
    expects_single_value: bool
    attempts: int
    clarification: str
    choices: list[str]
    checked: bool


FAILED = "The question could not be answered. Last error: "


class SqlReply(BaseModel):
    sql: str = Field(description="One SELECT statement, or an empty string when a clarification question is asked instead.")
    expects_single_value: bool = Field(description="True when the question asks for exactly one value, such as one overall average or one count; false for lists, tables or several values.")
    clarification: str = Field(description="One short question when the answer depends on something neither the question nor the database states; otherwise an empty string.")
    choices: list[str] = Field(description="2 to 3 complete questions, each keeping every condition of the original and filling in only the missing detail; empty when no clarification is asked.")


def combine(original: str, reply: str) -> str:
    return original + " (" + reply + ")"


def build_graph(model, db_path: str):
    # ponytail: one connection per node keeps state plain JSON-able data; share one if run time ever matters
    def load_schema(state: State):
        with closing(connect(db_path)) as conn:
            return {"schema": get_schema(conn)}

    def generate_sql(state: State):
        text = "Schema:\n" + state["schema"] + "\n\nQuestion: " + state["question"]
        if state.get("error"):
            text += ("\n\nThe previous query failed and must be corrected.\nPrevious SQL:\n"
                     + state["sql"] + "\nError: " + state["error"])
        reply = model.with_structured_output(SqlReply).invoke([("system", GENERATE_SQL_SYSTEM), ("user", text)])
        if reply.clarification.strip():   # a question back wins; any SQL in the reply is dropped
            return {"clarification": reply.clarification, "choices": reply.choices,
                    "status": "clarification", "answer": reply.clarification}
        if reply.sql.strip():
            return {"sql": reply.sql, "expects_single_value": reply.expects_single_value,
                    "attempts": state.get("attempts", 0) + 1}
        msg = "the model returned neither SQL nor a question"
        return {"error": msg, "status": "failed", "answer": FAILED + msg}

    def execute_sql(state: State):
        try:
            with closing(connect(db_path)) as conn:
                columns, rows, truncated = run_query(conn, state["sql"])
        except (ValueError, sqlite3.Error) as e:
            if state["attempts"] >= 2:
                return {"error": str(e), "status": "failed", "answer": FAILED + str(e)}
            return {"error": str(e)}
        return {"columns": columns, "rows": rows, "truncated": truncated, "error": ""}

    def check_result(state: State):
        rows, columns = state["rows"], state["columns"]
        if not rows:
            return {"status": "empty", "answer": "Nothing matched."}
        if state.get("expects_single_value") and (len(rows) != 1 or len(columns) != 1):
            what = (f"{len(rows)} rows" if len(rows) != 1 else f"{len(columns)} columns")
            return {"status": "mismatch",
                    "answer": f"The question asks for a single value, but the query returned {what}."}
        if len(rows) == 1 and all(v is None for v in rows[0]):
            return {"status": "no_value", "answer": "No value is available for " + ", ".join(columns) + "."}
        return {"checked": True}   # an empty update reaches the trace as None, which breaks it

    def answer(state: State):
        text = ("Question: " + state["question"] + "\n\nSQL:\n" + state["sql"]
                + "\n\nColumns: " + ", ".join(state["columns"])
                + "\nRows:\n" + "\n".join(
                    "(" + ", ".join(repr(v)[:200] for v in r) + ")" for r in state["rows"]))
        if state["truncated"]:
            text += "\n\nThe result shows only the first 100 rows."
        reply = model.invoke([("system", ANSWER_SYSTEM), ("user", text)])
        if not reply.text.strip():
            return {"error": "empty answer from model", "status": "failed",
                    "answer": FAILED + "empty answer from model"}
        return {"answer": reply.text, "status": "answered"}

    g = StateGraph(State)
    g.add_node("load_schema", load_schema)
    g.add_node("generate_sql", generate_sql)
    g.add_node("execute_sql", execute_sql)
    g.add_node("check_result", check_result)
    g.add_node("answer", answer)
    g.add_edge(START, "load_schema")
    g.add_edge("load_schema", "generate_sql")
    g.add_conditional_edges("generate_sql", lambda s: END if s.get("status") else "execute_sql")
    g.add_conditional_edges("execute_sql", lambda s: (
        ("generate_sql" if s["attempts"] < 2 else END) if s.get("error") else "check_result"))
    g.add_conditional_edges("check_result", lambda s: END if s.get("status") else "answer")
    g.add_edge("answer", END)
    return g.compile()
