import argparse
import subprocess
import sys
from pathlib import Path

from . import guard
from .ask import Answer, ask, context, execute

DEMO_APP = Path(__file__).with_name("demo_app.py")


def demo() -> None:
    """Start the local web demo on http://localhost:8501 and open the browser."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise SystemExit('The demo needs Streamlit: pip install -e ".[demo]"')
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(DEMO_APP), "--server.address", "localhost",
                    "--server.port", "8501", "--browser.gatherUsageStats", "false",
                    "--client.toolbarMode", "minimal",  # hides Streamlit's "Deploy" button
                    "--server.fileWatcherType", "none"])  # no code reloading needed, and no "install Watchdog" hint


def table(result: guard.Result) -> str:
    rows = [[str(c) for c in result.columns]] + [["NULL" if v is None else str(v) for v in r] for r in result.rows]
    widths = [min(max(len(r[i]) for r in rows), 40) for i in range(len(result.columns))]
    lines = [" | ".join(v[:40].ljust(w) for v, w in zip(r, widths)) for r in rows]
    lines.insert(1, "-+-".join("-" * w for w in widths))
    more = f" (first {len(result.rows)} rows shown)" if result.truncated else ""
    return "\n".join(lines) + f"\n{len(result.rows)} rows{more}, {result.ms} ms"


def show(a: Answer) -> int:
    if a.question and a.sql:  # typed SQL needs no echo and has no confidence
        print(f"SQL (confidence {a.confidence:.0%}):\n  {a.sql}")
    for x in a.assumptions:
        print(f"  assumes: {x}")
    if a.first_problems:
        print("  fixed in a second attempt: " + "; ".join(a.first_problems))
    if a.status == "done":
        print("\n" + table(a.result))
        return 0
    message = {"blocked": "BLOCKED by the guardrails", "invalid": "Not run, the query is broken",
               "declined": f"Claude declined: {a.reason}", "error": f"Error: {a.error}", "no key": a.error,
               "confirm": "Low confidence: not run"}[a.status]
    print(f"\n{message}")
    for p in a.problems:
        print(f"  ! {p}")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="text-to-sql", description="Ask the sample shop database questions in plain "
                                "English. Every query is checked by guardrails and runs read-only.")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("ask", help="question -> SQL with Claude (needs ANTHROPIC_API_KEY) -> result")
    q.add_argument("question")
    q.add_argument("--yes", action="store_true", help="run low-confidence queries without asking")
    sub.add_parser("run", help="run your own SQL through the guardrails (no key needed)").add_argument("sql")
    sub.add_parser("check", help="only check SQL against the guardrails").add_argument("sql")
    sub.add_parser("schema", help="print the schema and sample rows Claude sees")
    sub.add_parser("demo", help="start the local web demo on http://localhost:8501")
    args = p.parse_args(argv)

    if args.cmd == "demo":
        return demo()
    if args.cmd == "schema":
        print(context())
        return 0
    if args.cmd == "check":
        v = guard.check(args.sql)
        print("OK: one read-only SELECT over known tables and columns" if v.ok else
              ("BLOCKED" if v.unsafe else "INVALID") + "\n" + "\n".join(f"  ! {x}" for x in v.problems))
        return 0 if v.ok else 1
    if args.cmd == "run":
        return show(execute(Answer("", "", args.sql, 1.0)))
    a = ask(args.question)
    if a.status == "confirm":
        show(a)
        go = args.yes or (sys.stdin.isatty() and input("Run it anyway? [y/N] ").strip().lower() in ("y", "yes"))
        if not go:
            return 1
        a = execute(a)
    return show(a)


if __name__ == "__main__":
    sys.exit(main())
