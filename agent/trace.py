import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def run_traced(graph, question: str, *, database: str, provider: str, model: str, traces_dir: str = "traces") -> dict:
    now = datetime.now(timezone.utc)
    record = {"run_id": now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "started_at": now.isoformat(),
              "question": question, "database": database, "provider": provider, "model": model,
              "status": None, "answer": None, "steps": []}
    node, last = None, time.monotonic()

    def add(name, output):
        nonlocal last
        t = time.monotonic()
        record["steps"].append({"node": name, "at": datetime.now(timezone.utc).isoformat(),
                                "duration_ms": round((t - last) * 1000), "output": output})
        last = t

    try:
        for mode, chunk in graph.stream({"question": question}, stream_mode=["updates", "tasks"]):
            if mode == "tasks" and "result" not in chunk:
                node = chunk["name"]
            elif mode == "updates":
                for name, update in chunk.items():
                    add(name, update)
                    record["status"] = update.get("status", record["status"])
                    record["answer"] = update.get("answer", record["answer"])
    except Exception as e:
        # only the class name is kept: provider error text can carry request details
        add(node, {"error": type(e).__name__})
        record["status"] = "error"
        record["answer"] = f"Run stopped at {node}: {type(e).__name__}."
    Path(traces_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(traces_dir) / f"{record['run_id']}.json", "w") as f:
        json.dump(record, f, indent=2, default=repr)
    return record


def load_trace(run_id: str, traces_dir: str = "traces") -> dict:
    with open(Path(traces_dir) / f"{run_id}.json") as f:
        return json.load(f)


def format_trace(record: dict) -> str:
    lines = [f"Run {record['run_id']}", f"Started:  {record['started_at']}", f"Database: {record['database']}",
             f"Model:    {record['provider']} / {record['model']}", f"Status:   {record['status']}",
             f"Question: {record['question']}"]
    for step in record["steps"]:
        lines += ["", f"[{step['node']}] {step['duration_ms']} ms"]
        for key, value in step["output"].items():
            if key == "rows":
                lines += ["  rows:"] + [f"    {row}" for row in value]
            elif isinstance(value, str) and "\n" in value:
                lines += [f"  {key}:"] + [f"    {line}" for line in value.splitlines()]
            else:
                lines.append(f"  {key}: {value}")
    return "\n".join(lines)
