import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from agent.graph import build_graph
from agent.trace import format_trace, load_trace, run_traced


def main(argv: list[str] | None = None, model=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.cli")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--db", default="university.db")
    parser.add_argument("--traces", default="traces")
    parser.add_argument("--trace", metavar="RUN_ID")
    args = parser.parse_args(argv)

    if args.trace:
        try:
            print(format_trace(load_trace(args.trace, args.traces)))
        except FileNotFoundError:
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
        model = init_chat_model(model_name, model_provider=provider)
    else:
        provider = model_name = "injected"

    record = run_traced(build_graph(model, args.db), args.question, database=args.db,
                        provider=provider, model=model_name, traces_dir=args.traces)
    print(record["answer"])
    print()
    print(f"run: {record['run_id']}")
    return 0 if record["status"] == "answered" else 1


if __name__ == "__main__":
    sys.exit(main())
