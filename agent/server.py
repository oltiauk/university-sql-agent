import argparse
import json
import logging
import os
import re
import sqlite3
import math
import threading
import time
import urllib.parse
import urllib.request
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.db import connect
from agent.graph import build_graph, combine
from agent.trace import run_traced

PROVIDERS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "ollama": None}
UI = Path(__file__).with_name("ui.html")
MAX_BODY = 1_000_000
MAX_QUESTION = 2000
BROWSE_SECONDS = 5
MAX_CELL = 2000
MAX_BROWSE_ROWS = 1000   # ponytail: the viewer shows at most this many rows; page through or search if a table is ever larger
log = logging.getLogger("agent.server")


def fetch_ollama_tags() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as r:
        return json.load(r)


def default_create_model(provider, model_name, key):
    extra = {"api_key": key} if key else {}
    return init_chat_model(model_name, model_provider=provider, **extra)


def table_names(conn) -> dict:
    """{name: 'table' | 'view'} as sqlite_master lists them; the only names the viewer will put into SQL"""
    return dict(conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                             "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY rowid"))


def limited(conn):
    """the viewer's connection: bad UTF-8 is replaced, and a query running past BROWSE_SECONDS is interrupted"""
    conn.text_factory = lambda b: b.decode("utf-8", "replace")
    restart_deadline(conn)
    return conn


def restart_deadline(conn):
    deadline = time.monotonic() + BROWSE_SECONDS
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)


def cell(v):
    if isinstance(v, float) and not math.isfinite(v):
        return repr(v)
    if isinstance(v, str) and len(v) > MAX_CELL:
        return v[:MAX_CELL] + "…"
    if isinstance(v, bytes):
        return repr(v[:64]) + ("…" if len(v) > 64 else "")
    return v


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


CHAT_ID = re.compile(r"[0-9a-f]{12}")


def chat_runs(traces, db, chat):
    """the saved runs of one chat on this database, oldest first"""
    runs = []
    for f in sorted(Path(traces).glob("*.json")):
        try:
            record = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("database") == db and record.get("chat") == chat:
            runs.append(record)
    return sorted(runs, key=lambda r: r.get("started_at", ""))   # file names only sort to the second; ponytail: reads every trace file; index them if the folder gets large


def history_from(records) -> list[dict]:
    """the last three runs that did not stop with an error, as the model should see them"""
    out = []
    for r in [r for r in records if r["status"] != "error"][-3:]:
        sqls = [s["output"]["sql"] for s in r["steps"] if s["node"] == "generate_sql" and s["output"].get("sql")]
        keep = r["status"] in ("answered", "empty", "no_value", "mismatch")
        out.append({"question": r.get("understood") or r["question"], "answer": r["answer"],
                    "sql": sqls[-1] if sqls and keep else None})
    return out


def key_in_environment(provider) -> bool:
    return bool(os.environ.get(PROVIDERS[provider] or ""))


def make_server(db: str, traces: str = "traces", examples: list[str] = (), port: int = 0, create_model=None):
    create_model = create_model or default_create_model
    # ponytail: one model for the whole process; give each browser session its own if more than one person ever uses it
    state = {"model": None, "provider": None, "model_name": None}
    lock = threading.Lock()

    def set_model(provider, model_name, key):
        model = create_model(provider, model_name, key)   # the key goes no further than this call
        with lock:
            state.update(model=model, provider=provider, model_name=model_name)

    provider, model_name = os.environ.get("LLM_PROVIDER"), os.environ.get("LLM_MODEL")
    if provider in PROVIDERS and model_name and (PROVIDERS[provider] is None or key_in_environment(provider)):
        try:
            set_model(provider, model_name, None)
        except Exception as e:
            log.warning("Could not create the model from the environment: %s", type(e).__name__)

    class Handler(BaseHTTPRequestHandler):
        timeout = 10   # a body shorter than its Content-Length would otherwise hold the thread forever

        def log_message(self, format, *args):
            log.info("%s %s", self.address_string(), format % args)   # request line only, never a body

        def send(self, status, body):
            html = isinstance(body, bytes)
            data = body if html else json.dumps(body, default=repr).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def handle_request(self, method):
            try:
                host = self.server.server_address[1]
                if self.headers.get("Host") not in (f"127.0.0.1:{host}", f"localhost:{host}"):
                    return self.send(403, {"error": "Forbidden host"})
                body = None
                if method == "POST":
                    if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                        return self.send(415, {"error": "Content-Type must be application/json"})
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                        if not 0 <= length <= MAX_BODY:
                            raise ValueError
                        body = json.loads(self.rfile.read(length))
                        if not isinstance(body, dict):
                            raise ValueError
                    except ValueError:
                        return self.send(400, {"error": "Invalid JSON body"})
                self.send(*self.route(method, self.path.split("?")[0], body))
            except Exception as e:
                self.send(500, {"error": type(e).__name__})   # class name only, never the message

        def do_GET(self):
            self.handle_request("GET")

        def do_POST(self):
            self.handle_request("POST")

        def route(self, method, path, body):
            if (method, path) == ("GET", "/"):
                return 200, UI.read_bytes()
            if (method, path) == ("GET", "/api/state"):
                with lock:
                    snap = dict(state)
                return 200, {"database": Path(db).name, "ready": snap["model"] is not None,
                             "provider": snap["provider"], "model": snap["model_name"],
                             "providers": [{"id": p, "needs_key": env is not None, "key_in_environment": key_in_environment(p)}
                                           for p, env in PROVIDERS.items()],
                             "default_provider": os.environ.get("LLM_PROVIDER") or None,
                             "default_model": os.environ.get("LLM_MODEL") or None,
                             "examples": list(examples)}
            if (method, path) == ("GET", "/api/ollama"):
                try:
                    return 200, {"running": True, "models": [m["name"] for m in fetch_ollama_tags()["models"]]}
                except Exception:
                    return 200, {"running": False, "models": []}
            if (method, path) == ("GET", "/api/runs"):
                chat = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("chat", [""])[0]
                return 200, {"runs": chat_runs(traces, db, chat)[-20:] if CHAT_ID.fullmatch(chat) else []}
            if (method, path) == ("GET", "/api/tables"):
                tables = []
                with closing(limited(connect(db))) as conn:
                    for name, kind in table_names(conn).items():
                        restart_deadline(conn)
                        try:
                            rows = conn.execute(f"SELECT COUNT(*) FROM {quoted(name)}").fetchone()[0]
                        except sqlite3.Error:
                            rows = None   # a broken view is still listed; opening it shows the error
                        tables.append({"name": name, "kind": kind, "rows": rows})
                return 200, {"tables": tables}
            if (method, path) == ("GET", "/api/table"):
                return self._table(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name", [None])[0])
            if (method, path) == ("POST", "/api/setup"):
                return self._setup(body)
            if (method, path) == ("POST", "/api/ask"):
                return self._ask(body)
            return 404, {"error": "Not found"}

        def _table(self, name):
            if not name:
                return 400, {"error": "Missing name"}
            with closing(limited(connect(db))) as conn:
                if name not in table_names(conn):   # only an exact name from sqlite_master reaches the SQL below
                    return 404, {"error": "No such table"}
                try:
                    total = conn.execute(f"SELECT COUNT(*) FROM {quoted(name)}").fetchone()[0]
                    cur = conn.execute(f"SELECT * FROM {quoted(name)} LIMIT {MAX_BROWSE_ROWS}")
                    rows = cur.fetchall()
                except sqlite3.Error as e:
                    return 400, {"error": type(e).__name__}   # class name only, never the message
            return 200, {"name": name, "columns": [d[0] for d in cur.description], "rows": [[cell(v) for v in r] for r in rows],
                         "total": total, "truncated": total > MAX_BROWSE_ROWS}

        def _setup(self, body):
            provider, name, key = body.get("provider"), body.get("model"), body.get("key")
            if not isinstance(provider, str) or provider not in PROVIDERS:
                return 400, {"error": "Unknown provider"}
            if not isinstance(name, str) or not name.strip():
                return 400, {"error": "Enter a model name"}
            key = key.strip() if isinstance(key, str) and key.strip() else None
            if PROVIDERS[provider] and not key and not key_in_environment(provider):
                return 400, {"error": f"{provider} needs a key: type one or set {PROVIDERS[provider]}"}
            try:
                set_model(provider, name.strip(), key)
            except Exception as e:
                return 400, {"error": f"Could not create the model: {type(e).__name__}"}
            return 200, {"ok": True}

        def _ask(self, body):
            with lock:
                model, provider, name = state["model"], state["provider"], state["model_name"]
            if model is None:
                return 409, {"error": "Choose a model first"}
            if "original" in body or "reply" in body:
                original, reply = body.get("original"), body.get("reply")
                ok = isinstance(original, str) and isinstance(reply, str) and original.strip() and reply.strip()
                question = combine(original, reply) if ok else None
            else:
                question = body.get("question")
            if not isinstance(question, str) or not question.strip():
                return 400, {"error": "Enter a question"}
            if len(question) > MAX_QUESTION:
                return 400, {"error": "The question is too long"}
            chat = body.get("chat")
            if chat is not None and not (isinstance(chat, str) and CHAT_ID.fullmatch(chat)):
                return 400, {"error": "Bad chat id"}
            history = history_from(chat_runs(traces, db, chat)) if chat else []
            graph = build_graph(model, db)   # one graph per ask, so two asks can run together
            return 200, run_traced(graph, question, database=db, provider=provider, model=name, traces_dir=traces,
                                    history=history, chat=chat)

    # ponytail: binds 127.0.0.1 only, so no auth; add a token before it is ever exposed beyond this computer
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)   # its threads are daemon threads already


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.server")
    parser.add_argument("--db", default="university.db")
    parser.add_argument("--traces", default="traces")
    parser.add_argument("--examples", metavar="FILE")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    load_dotenv()
    examples = []
    if args.examples:
        examples = [line.strip() for line in Path(args.examples).read_text().splitlines() if line.strip()]
    server = make_server(args.db, args.traces, examples, args.port)
    print(f"Open http://127.0.0.1:{server.server_address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
