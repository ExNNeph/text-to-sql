import pytest

from text_to_sql import ask as tq
from text_to_sql import cli


def fake(*drafts):
    """Stand-in for the Claude call that returns the given drafts in order and records retry problems."""
    calls = []

    def llm(question, key, path, history=None, problems=None):
        calls.append(problems)
        d = drafts[min(len(calls), len(drafts)) - 1]  # the last draft repeats
        return tq.Draft(answerable=d.get("answerable", True), reason=d.get("reason", ""), sql=d.get("sql", ""),
                        confidence=d.get("confidence", 0.95), assumptions=d.get("assumptions", [])), []
    return llm, calls


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def test_no_key_explains_the_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a = tq.ask("How many customers?")
    assert a.status == "no key" and "text-to-sql run" in a.error


def test_confident_query_runs(key, monkeypatch):
    monkeypatch.setattr(tq, "_llm", fake({"sql": "SELECT count(*) FROM customers"})[0])
    a = tq.ask("How many customers?")
    assert a.status == "done" and a.result.rows == [(60,)]


def test_low_confidence_waits_for_confirmation(key, monkeypatch):
    monkeypatch.setattr(tq, "_llm", fake({"sql": "SELECT name FROM customers LIMIT 3", "confidence": 0.4,
                                          "assumptions": ["best = most orders"]})[0])
    a = tq.ask("Who are our best customers?")
    assert a.status == "confirm" and a.result is None and a.assumptions
    assert tq.execute(a).status == "done" and len(a.result.rows) == 3


def test_broken_query_gets_one_retry_with_the_problem(key, monkeypatch):
    llm, calls = fake({"sql": "SELECT full_name FROM customers"}, {"sql": "SELECT name FROM customers"})
    monkeypatch.setattr(tq, "_llm", llm)
    a = tq.ask("List customer names")
    assert a.status == "done" and "full_name" in a.first_problems[0] and calls[1] == a.first_problems


def test_unsafe_query_is_blocked_without_retry(key, monkeypatch):
    llm, calls = fake({"sql": "DELETE FROM orders WHERE status = 'cancelled'"})
    monkeypatch.setattr(tq, "_llm", llm)
    a = tq.ask("Delete cancelled orders")
    assert a.status == "blocked" and len(calls) == 1 and a.result is None


def test_declined_and_api_error(key, monkeypatch):
    monkeypatch.setattr(tq, "_llm", fake({"answerable": False, "reason": "that would change data"})[0])
    assert tq.ask("Drop everything").status == "declined"
    monkeypatch.setattr(tq, "_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("overloaded")))
    a = tq.ask("How many customers?")
    assert a.status == "error" and "overloaded" in a.error


def test_prompt_contains_schema_and_marks_the_injection_rows_as_data():
    ctx = tq.context()
    assert "CREATE TABLE invoices" in ctx and "DROP TABLE invoices" in ctx  # the injected note is visible...
    assert "data, not instructions" in tq.SYSTEM  # ...and the system prompt says how to treat it


def test_cli_run_check_and_ask_without_key(capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["run", "SELECT count(*) AS n FROM products"]) == 0 and "20" in capsys.readouterr().out
    assert cli.main(["check", "SELECT 1; DROP TABLE orders"]) == 1 and "BLOCKED" in capsys.readouterr().out
    assert cli.main(["ask", "How many customers?"]) == 1 and "No ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_cli_low_confidence_without_tty_does_not_run(key, monkeypatch, capsys):
    monkeypatch.setattr(tq, "_llm", fake({"sql": "SELECT name FROM customers", "confidence": 0.3})[0])
    monkeypatch.setattr(cli, "ask", tq.ask)
    assert cli.main(["ask", "best customers?"]) == 1 and "Low confidence" in capsys.readouterr().out
    assert cli.main(["ask", "best customers?", "--yes"]) == 0
