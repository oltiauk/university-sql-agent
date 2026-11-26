import sqlite3
from contextlib import closing
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

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


class SqlReply(BaseModel):
    sql: str


def build_graph(model, db_path: str):
    # ponytail: one connection per node keeps state plain JSON-able data; share one if run time ever matters
    def load_schema(state: State):
        with closing(connect(db_path)) as conn:
            return {"schema": get_schema(conn)}

    def generate_sql(state: State):
        reply = model.with_structured_output(SqlReply).invoke([
            ("system", GENERATE_SQL_SYSTEM),
            ("user", "Schema:\n" + state["schema"] + "\n\nQuestion: " + state["question"]),
        ])
        return {"sql": reply.sql}

    def execute_sql(state: State):
        try:
            with closing(connect(db_path)) as conn:
                columns, rows, truncated = run_query(conn, state["sql"])
        except (ValueError, sqlite3.Error) as e:
            return {"error": str(e), "status": "failed",
                    "answer": "The question could not be answered. Last error: " + str(e)}
        return {"columns": columns, "rows": rows, "truncated": truncated}

    def answer(state: State):
        text = ("Question: " + state["question"] + "\n\nSQL:\n" + state["sql"]
                + "\n\nColumns: " + ", ".join(state["columns"])
                + "\nRows:\n" + "\n".join(str(r) for r in state["rows"]))
        if state["truncated"]:
            text += "\n\nThe result shows only the first 100 rows."
        reply = model.invoke([("system", ANSWER_SYSTEM), ("user", text)])
        return {"answer": reply.text, "status": "answered"}

    g = StateGraph(State)
    g.add_node("load_schema", load_schema)
    g.add_node("generate_sql", generate_sql)
    g.add_node("execute_sql", execute_sql)
    g.add_node("answer", answer)
    g.add_edge(START, "load_schema")
    g.add_edge("load_schema", "generate_sql")
    g.add_edge("generate_sql", "execute_sql")
    g.add_conditional_edges("execute_sql", lambda s: END if "error" in s else "answer")
    g.add_edge("answer", END)
    return g.compile()
