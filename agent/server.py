import argparse
import json
import logging
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.graph import build_graph, combine
from agent.trace import run_traced

PROVIDERS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "ollama": None}
UI = Path(__file__).with_name("ui.html")
MAX_BODY = 1_000_000
MAX_QUESTION = 2000
log = logging.getLogger("agent.server")


def fetch_ollama_tags() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as r:
        return json.load(r)


def default_create_model(provider, model_name, key):
    extra = {"api_key": key} if key else {}
    return init_chat_model(model_name, model_provider=provider, **extra)


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
                runs = []
                for f in sorted(Path(traces).glob("*.json")):
                    try:
                        record = json.loads(f.read_text())
                    except (OSError, ValueError):
                        continue
                    if isinstance(record, dict) and record.get("database") == db:
                        runs.append(record)
                return 200, {"runs": runs[-20:]}   # ponytail: reads every trace file; index them if the folder gets large
            if (method, path) == ("POST", "/api/setup"):
                return self._setup(body)
            if (method, path) == ("POST", "/api/ask"):
                return self._ask(body)
            return 404, {"error": "Not found"}

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
            graph = build_graph(model, db)   # one graph per ask, so two asks can run together
            return 200, run_traced(graph, question, database=db, provider=provider, model=name, traces_dir=traces)

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
