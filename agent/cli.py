import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.graph import build_graph, combine
from agent.trace import format_trace, load_trace, run_traced


def main(argv: list[str] | None = None, model=None, ask=input) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.cli")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--db", default="university.db")
    parser.add_argument("--traces", default="traces")
    parser.add_argument("--trace", metavar="RUN_ID")
    args = parser.parse_args(argv)

    if args.trace:
        try:
            if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", args.trace):
                raise FileNotFoundError
            print(format_trace(load_trace(args.trace, args.traces)))
        except (OSError, ValueError, KeyError):
            print(f"No trace found for run {args.trace}", file=sys.stderr)
            return 2
        return 0
    if not args.question:
        print("Give a question to ask, or --trace RUN_ID", file=sys.stderr)
        return 2
    if not Path(args.db).is_file():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 2

    if model is None:
        load_dotenv()
        for name in ("LLM_PROVIDER", "LLM_MODEL"):
            if not os.environ.get(name):
                print(f"Set {name} in the environment or in .env (see .env.example)", file=sys.stderr)
                return 2
        provider, model_name = os.environ["LLM_PROVIDER"], os.environ["LLM_MODEL"]
        try:
            model = init_chat_model(model_name, model_provider=provider)
        except Exception as e:
            print(f"Could not create the model: {type(e).__name__}. "
                  "Check LLM_PROVIDER, LLM_MODEL and the provider's key.", file=sys.stderr)
            return 2
    else:
        provider = model_name = "injected"

    graph = build_graph(model, args.db)
    question = args.question
    while True:
        record = run_traced(graph, question, database=args.db,
                            provider=provider, model=model_name, traces_dir=args.traces)
        print(record["answer"])
        choices = (record["steps"] or [{"output": {}}])[-1]["output"].get("choices", [])
        for i, choice in enumerate(choices, 1):
            print(f"{i}. {choice}")
        print()
        print(f"run: {record['run_id']}")
        if record["status"] != "clarification":
            break
        try:
            reply = ask("Pick a number or type your answer (Enter to stop): ").strip()
        except EOFError:
            break
        if not reply:
            break
        picked = {str(i): c for i, c in enumerate(choices, 1)}.get(reply)   # reply is already stripped
        question = picked if picked is not None else combine(args.question, reply)
    return 1 if record["status"] in ("failed", "mismatch", "error") else 0


if __name__ == "__main__":
    sys.exit(main())
