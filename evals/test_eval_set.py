"""Sanity checks on the labeled questions, no API calls."""
import json
from collections import Counter
from pathlib import Path

from run_eval import matches
from text_to_sql import guard

CASES = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))


def test_set_shape():
    assert Counter(c["kind"] for c in CASES) == {"safe": 30, "ambiguous": 5, "attack": 20}
    assert len({c["id"] for c in CASES}) == len(CASES) and len({c["question"] for c in CASES}) == len(CASES)
    assert set(Counter(c["attack"] for c in CASES if c["kind"] == "attack")) == {"destructive", "escape", "injection"}


def test_gold_sql_passes_the_guardrails_and_gives_the_labeled_rows():
    for c in (c for c in CASES if c["kind"] == "safe"):
        assert guard.check(c["gold_sql"]).ok, c["gold_sql"]
        assert c["expected"], c["question"]  # an empty answer would let wrong queries pass
        assert matches(guard.run(c["gold_sql"]).rows, c["expected"], c["ordered"]), c["question"]


def test_matcher():
    assert matches([(7, "Anna", 3.0000001)], [["Anna", 3]], False)  # extra column, float noise
    assert matches([("b",), ("a",)], [["a"], ["b"]], False) and not matches([("b",), ("a",)], [["a"], ["b"]], True)
    assert not matches([("a",)], [["a"], ["b"]], False) and not matches([], [["a"]], False)
    assert not matches([("a", 1)], [["a", 2]], False)
