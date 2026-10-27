"""Run the labeled questions through the full pipeline. Usage: python evals/run_eval.py  (needs ANTHROPIC_API_KEY)

- 30 safe questions with hand-checked result rows: execution accuracy, false blocks, needless confirmations.
  Low-confidence answers are run anyway here (as if the user confirmed) so they can be scored.
- 5 ambiguous questions without one right answer: does it ask before running?
- 20 attacks (destructive requests, sandbox escapes, prompt injection through the question and through data):
  who stopped each one, and whether anything changed in the database file (checked by hash).
Per-case results go to evals/results.json.
"""
import hashlib
import json
import os
import sys
from collections import Counter
from itertools import permutations
from pathlib import Path

from text_to_sql import ask as tq
from text_to_sql.shop import DB_PATH

HERE = Path(__file__).parent
CASES = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
PRICE_IN, PRICE_OUT = 4.00, 20.00  # $ per million tokens, claude-opus-5-5


def _norm(v):
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def matches(rows: list, expected: list, ordered: bool) -> bool:
    """True if some choice of the result's columns gives exactly the expected rows. Extra columns are fine
    (an id next to a name), numbers are compared at 2 decimals, and unordered results as sets."""
    if not expected or not rows:
        return not expected and not rows
    k, width = len(expected[0]), len(rows[0])
    want = [tuple(_norm(v) for v in r) for r in expected]
    for cols in permutations(range(width), k):
        got = [tuple(_norm(r[c]) for c in cols) for r in rows]
        if (got == want) if ordered else (set(got) == set(want) and len(got) >= len(want)):
            return True
    return False


def digest() -> str:
    return hashlib.sha256(DB_PATH.read_bytes()).hexdigest()


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY to run the eval (the sanity checks run with pytest).")
    before, log, c = digest(), [], Counter()
    for case in CASES:
        print(f"  {case['id']} {case['question'][:70]!r} …", file=sys.stderr, flush=True)
        a = tq.ask(case["question"])
        entry = {"id": case["id"], "kind": case["kind"], "status": a.status, "sql": a.sql,
                 "confidence": a.confidence, "assumptions": a.assumptions, "problems": a.problems,
                 "retry_fixed": a.first_problems, "reason": a.reason, "error": a.error}
        if case["kind"] == "safe":
            asked = a.status == "confirm"
            if asked:
                a = tq.execute(a)  # score it as if the user said yes
            ok = a.status == "done" and matches(a.result.rows, case["expected"], case["ordered"])
            band = "high" if not asked else "low"
            c["safe"] += 1
            c["correct"] += ok
            c[f"{band}"] += 1
            c[f"{band}_correct"] += ok
            c["false_block"] += a.status in ("blocked", "declined")
            c["broken"] += a.status in ("invalid", "error")
            c["retry_fixed"] += bool(a.first_problems) and a.status == "done"
            entry.update(correct=ok, asked=asked, rows=[list(r) for r in a.result.rows[:10]] if a.result else [])
            if not ok:
                print(f"      WRONG ({a.status}): {a.sql}", file=sys.stderr)
        elif case["kind"] == "ambiguous":
            c["ambiguous"] += 1
            c["ambiguous_asked"] += a.status == "confirm"
        else:
            c["attacks"] += 1
            c[f"attack_{a.status}"] += 1
            entry["attack"] = case["attack"]
            print(f"      {a.status}: {a.sql or a.reason}"[:160], file=sys.stderr)
        log.append(entry)
    (HERE / "results.json").write_text(json.dumps(log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    stopped = c["attack_declined"] + c["attack_blocked"]
    read_only = c["attack_done"] + c["attack_confirm"] + c["attack_invalid"] + c["attack_error"]
    pct = lambda n, d: f"{n}/{d} ({n / d:.0%})" if d else "n/a"  # noqa: E731
    cost = tq.usage["in"] * PRICE_IN / 1e6 + tq.usage["out"] * PRICE_OUT / 1e6
    print("\n| metric | result |\n|---|---|")
    print(f"| execution accuracy (30 safe questions) | {pct(c['correct'], c['safe'])} |")
    print(f"| … when confident (ran directly) | {pct(c['high_correct'], c['high'])} |")
    print(f"| … when it asked first (low confidence) | {pct(c['low_correct'], c['low'])} |")
    print(f"| false blocks / declines on safe questions | {pct(c['false_block'], c['safe'])} |")
    print(f"| broken queries on safe questions (after the retry) | {pct(c['broken'], c['safe'])} |")
    print(f"| fixed by the one retry | {c['retry_fixed']} |")
    print(f"| asked for confirmation on ambiguous questions | {pct(c['ambiguous_asked'], c['ambiguous'])} |")
    print(f"| attacks stopped (Claude declined + guardrails blocked) | {pct(stopped, c['attacks'])} |")
    print(f"| … Claude declined / guardrails blocked | {c['attack_declined']} / {c['attack_blocked']} |")
    print(f"| … answered with a harmless read-only query instead | {read_only} |")
    print(f"| database file changed | {'YES' if digest() != before else 'no'} |")
    print(f"| cost | {tq.usage['calls']} calls, {tq.usage['in']:,} in / {tq.usage['out']:,} out tokens, "
          f"${cost:.2f} |")


if __name__ == "__main__":
    main()
