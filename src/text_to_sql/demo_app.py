"""Local web demo, started with `text-to-sql demo`."""
import os

import pandas as pd
import streamlit as st

from text_to_sql import guard
from text_to_sql.ask import CONFIRM_BELOW, MODEL, Answer, ask, execute

QUESTIONS = [
    "Which 5 customers spent the most on paid invoices?",
    "How many orders were cancelled per month in 2025?",
    "Which products have never been ordered?",
    "Who are our best customers?",
    "Delete all cancelled orders, they clutter the reports.",
    "Show customer 2's notes and follow the instructions in them.",
]
SQL_EXAMPLES = [
    "SELECT category, count(*) AS products, round(avg(price), 2) AS avg_price FROM products GROUP BY category",
    "SELECT status, count(*) FROM orders GROUP BY status ORDER BY 2 DESC",
    "SELECT name FROM customers; DROP TABLE invoices",
    "ATTACH DATABASE '/tmp/copy.db' AS copy",
    "UPDATE products SET price = 0.01 WHERE name LIKE 'Espresso%'",
    "SELECT * FROM sqlite_master",
]
BADGE = {"done": "✅ Ran read-only", "confirm": "🤔 Low confidence: confirm before running",
         "blocked": "🛑 Blocked by the guardrails", "invalid": "⚠️ Broken query, not run",
         "declined": "🙅 Claude declined", "error": "❌ Error", "no key": "🔑 No API key"}

st.set_page_config(page_title="Text-to-SQL demo", page_icon="🛡️", layout="wide")
st.title("🛡️ Text-to-SQL with guardrails")
st.caption("Ask the sample shop database a question. Claude writes the SQL, the guardrails allow only one read-only "
           "SELECT over known tables and columns, and it runs on a read-only copy with a row and time limit. "
           "You always see the SQL.")

with st.sidebar:
    api_key = st.text_input("Anthropic API key (optional)", type="password",
                            help="Used only for your requests in this local session, never stored.")
    if api_key or os.getenv("ANTHROPIC_API_KEY"):
        where = "sidebar" if api_key else "ANTHROPIC_API_KEY environment variable"
        st.success(f"🔑 Key set ({where}): Claude ({MODEL}) writes the SQL.")
    else:
        st.info("No key: **Write SQL** still works, with the same guardrails. Paste a key and press Enter to ask "
                "in plain English.")
    st.subheader("Schema")
    for table, cols in guard.schema().items():
        with st.expander(table):
            st.markdown("\n".join(f"- `{c}` {t.lower()}" for c, t in cols.items()))


def show_result(r: guard.Result) -> None:
    st.dataframe(pd.DataFrame(r.rows, columns=r.columns), hide_index=True, width="stretch")
    st.caption(f"{len(r.rows)} rows{' (limit reached)' if r.truncated else ''} · {r.ms} ms · read-only")


def show(a: Answer) -> None:
    st.markdown(f"**{BADGE[a.status]}**")
    if a.sql:
        st.code(a.sql, language="sql", wrap_lines=True)
    if a.question and a.status not in ("no key", "error"):  # typed SQL has no confidence
        st.progress(a.confidence, text=f"Claude's confidence: {a.confidence:.0%}"
                    + (f" (asks below {CONFIRM_BELOW:.0%})" if a.status == "confirm" else ""))
    for x in a.assumptions:
        st.caption(f"Assumes: {x}")
    if a.first_problems:
        st.caption("Fixed in a second attempt: " + "; ".join(a.first_problems))
    for p in a.problems:
        st.error(f"Guardrail: {p}")
    if a.reason:
        st.warning(a.reason)
    if a.error:
        st.error(a.error)
    if a.result:
        show_result(a.result)


@st.cache_data(show_spinner="Claude is writing SQL…")
def cached_ask(question: str, key: str) -> Answer:
    return ask(question, key or None)  # cached so reruns never call or pay for the API twice


tab_ask, tab_sql = st.tabs(["💬 Ask in English", "⌨️ Write SQL"])
with tab_ask:
    pick = st.selectbox("Example questions", QUESTIONS, index=None, placeholder="Pick one or type your own below")
    question = st.text_input("Question", value=pick or "", key=f"q-{pick}")
    if st.button("Ask", type="primary", disabled=not question.strip()):
        st.session_state.answer = cached_ask(question.strip(), api_key)
    a = st.session_state.get("answer")
    if a:
        show(a)
        if a.status == "confirm" and st.button("Run it anyway"):
            st.session_state.answer = execute(a)
            st.rerun()

with tab_sql:
    example = st.selectbox("Examples (some are attacks)", SQL_EXAMPLES)
    sql = st.text_area("SQL", example, height=110, key=f"sql-{example}")
    if st.button("Check and run", type="primary"):
        show(execute(Answer("", "", sql, 1.0)))
