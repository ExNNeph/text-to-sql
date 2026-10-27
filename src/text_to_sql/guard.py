"""Guardrails and the sandboxed runner. Every query, from Claude or typed by hand, passes `check()` before `run()`.

check(): parses the SQL with sqlglot and allows exactly one read-only SELECT (UNION and WITH included) over the
tables and columns in the schema. run(): opens the database file read-only (`mode=ro`), lets an SQLite authorizer
deny anything but reading, and stops at a row limit and a time limit. Either layer alone would stop a write; both
run so that a gap in one is not a hole.
"""
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify

from .shop import DB_PATH

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # it warns before falling back to an opaque Command
MAX_ROWS = 200
TIMEOUT_S = 3.0
WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter, exp.TruncateTable,
          exp.Attach, exp.Detach, exp.Pragma, exp.Command, exp.Transaction, exp.Commit, exp.Rollback, exp.Set)
# SQLite functions that touch files, load code or allocate unbounded memory; the authorizer denies them too
DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "zeroblob", "randomblob"}


@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)
    unsafe: bool = False  # a write or an escape attempt, never retried; otherwise just a broken query


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False  # more than MAX_ROWS rows; only the first MAX_ROWS are returned
    ms: int = 0


@cache
def schema(path: Path = DB_PATH) -> dict[str, dict[str, str]]:
    """{table: {column: type}} read from the database itself."""
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY rowid")]
        return {t: {c[1]: c[2] for c in con.execute(f"PRAGMA table_info({t})")} for t in tables}
    finally:
        con.close()


def check(sql: str, path: Path = DB_PATH) -> Verdict:
    tables = schema(path)
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except ParseError as e:
        return Verdict(False, [f"not valid SQLite SQL: {str(e).splitlines()[0]}"])
    if not statements:
        return Verdict(False, ["empty query"])
    if len(statements) > 1:
        kinds = ", ".join(type(s).__name__.upper() for s in statements)
        return Verdict(False, [f"{len(statements)} statements ({kinds}); only one SELECT is allowed"], unsafe=True)
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        if isinstance(tree, WRITES):
            return Verdict(False, [f"{_kind(tree)} is not allowed, only SELECT"], unsafe=True)
        return Verdict(False, ["not a SELECT statement"])
    if writes := [n for n in tree.walk() if isinstance(n, WRITES)]:
        return Verdict(False, [f"{_kind(writes[0])} inside the query is not allowed"], unsafe=True)
    problems, unknown = [], []
    for f in tree.find_all(exp.Func):
        name = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if name in DENIED_FUNCTIONS:
            problems.append(f"function {name}() is not allowed")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    known = {t.lower() for t in tables}
    for t in tree.find_all(exp.Table):
        if t.db and t.db.lower() != "main":
            problems.append(f"schema '{t.db}' is not allowed")
        elif t.name.lower().startswith("sqlite_"):
            problems.append(f"internal table '{t.name}' is not allowed")
        elif t.name.lower() not in known | ctes:
            unknown.append(f"unknown table '{t.name}'")
    if problems:
        return Verdict(False, problems, unsafe=True)
    if unknown:
        return Verdict(False, unknown)
    try:  # resolves every column against the schema, through aliases, subqueries and CTEs
        qualify(tree.copy(), schema=tables, dialect="sqlite", validate_qualify_columns=True)
    except Exception as e:  # OptimizeError usually; anything unexpected also blocks
        return Verdict(False, [f"unknown column: {e}"])
    return Verdict(True)


def _kind(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        return str(node.this).upper()  # e.g. VACUUM, REPLACE
    return type(node).__name__.upper().replace("TRUNCATETABLE", "TRUNCATE")


def _authorize(action, arg1, arg2, db, trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() not in DENIED_FUNCTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run(sql: str, path: Path = DB_PATH, max_rows: int = MAX_ROWS, timeout_s: float = TIMEOUT_S) -> Result:
    """Run one query that passed check(). Raises sqlite3.Error on a denied action, a runtime error or the timeout."""
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        con.set_authorizer(_authorize)
        start = time.monotonic()
        con.set_progress_handler(lambda: time.monotonic() - start > timeout_s, 10_000)  # non-zero aborts
        cur = con.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        return Result([d[0] for d in cur.description or []], rows[:max_rows], len(rows) > max_rows,
                      int((time.monotonic() - start) * 1000))
    except sqlite3.OperationalError as e:
        if str(e) == "interrupted":
            raise sqlite3.OperationalError(f"query stopped after {timeout_s:g} s") from None
        raise
    finally:
        con.close()
