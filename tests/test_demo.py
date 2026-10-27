import builtins

import pytest
from streamlit.testing.v1 import AppTest

from text_to_sql import ask as tq
from text_to_sql import cli


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return AppTest.from_file(str(cli.DEMO_APP), default_timeout=20)  # the file `text-to-sql demo` starts


def test_sql_tab_runs_safe_and_blocks_attacks_without_key(app):
    at = app.run()
    assert not at.exception
    at.button[1].click().run()  # first example: a safe GROUP BY
    assert any("Ran read-only" in m.value for m in at.markdown) and at.dataframe
    at.selectbox[1].select("SELECT name FROM customers; DROP TABLE invoices").run()
    at.button[1].click().run()
    assert any("Blocked" in m.value for m in at.markdown) and any("2 statements" in e.value for e in at.error)


def test_ask_tab_confirms_low_confidence(app, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(tq, "_llm", lambda *a, **k: (tq.Draft(
        answerable=True, reason="", sql="SELECT name FROM customers LIMIT 5", confidence=0.5,
        assumptions=["best = most orders"]), []))
    at = app.run()
    at.text_input[0].input("Who are our best customers?").run()
    at.button[0].click().run()
    assert at.session_state["answer"].status == "confirm" and not at.dataframe
    next(b for b in at.button if b.label == "Run it anyway").click().run()
    assert at.session_state["answer"].status == "done" and at.dataframe


def test_demo_without_streamlit_gives_install_hint(monkeypatch):
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "streamlit" else real(n, *a, **k))
    with pytest.raises(SystemExit, match="pip install"):
        cli.demo()
