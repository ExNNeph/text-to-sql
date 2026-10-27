import hashlib
import shutil
import sqlite3

import pytest

from text_to_sql import guard, shop

SAFE = [
    "SELECT name FROM customers",
    "select c.name, round(sum(i.amount), 2) total from customers c join orders o on o.customer_id = c.id "
    "join invoices i on i.order_id = o.id group by c.id order by total desc limit 5",
    "WITH big AS (SELECT order_id FROM invoices WHERE amount > 500) SELECT count(*) FROM big",
    "SELECT name FROM customers UNION SELECT name FROM products",
    "SELECT name FROM products WHERE id NOT IN (SELECT product_id FROM order_items)",
    "SELECT strftime('%Y-%m', order_date) m, count(*) FROM orders GROUP BY m",
    "SELECT * FROM main.products",
]
UNSAFE = [
    "DROP TABLE customers",
    "DELETE FROM orders WHERE status = 'cancelled'",
    "UPDATE products SET price = 0",
    "INSERT INTO customers (name) VALUES ('x')",
    "REPLACE INTO products (id, name) VALUES (1, 'x')",
    "ALTER TABLE customers ADD COLUMN x TEXT",
    "CREATE TEMP TABLE x AS SELECT * FROM customers",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "DETACH DATABASE main",
    "PRAGMA writable_schema = 1",
    "PRAGMA table_info(customers)",
    "VACUUM INTO '/tmp/copy.db'",
    "BEGIN; DELETE FROM invoices; COMMIT",
    "SELECT name FROM customers; DROP TABLE invoices",
    "SELECT 1; SELECT 2",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM temp.sqlite_temp_master",
    "SELECT load_extension('/tmp/evil')",
    "SELECT zeroblob(1000000000)",
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
]


@pytest.mark.parametrize("sql", SAFE)
def test_safe_queries_pass_and_run(sql):
    assert guard.check(sql).ok, guard.check(sql).problems
    assert guard.run(sql).columns


@pytest.mark.parametrize("sql", UNSAFE)
def test_unsafe_queries_are_blocked(sql):
    v = guard.check(sql)
    assert not v.ok and v.unsafe, (sql, v)


@pytest.mark.parametrize("sql, problem", [
    ("SELECT nope FROM customers", "unknown column"),
    ("SELECT c.price FROM customers c", "unknown column"),
    ("SELECT * FROM users", "unknown table 'users'"),
    ("SELEC name FROM customers", "not"),
    ("", "empty"),
])
def test_broken_queries_are_invalid_not_unsafe(sql, problem):
    v = guard.check(sql)
    assert not v.ok and not v.unsafe and problem in v.problems[0]


@pytest.mark.parametrize("sql", ["DELETE FROM orders", "DROP TABLE customers", "UPDATE products SET price = 0",
                                 "ATTACH DATABASE ':memory:' AS x", "SELECT load_extension('x')"])
def test_runner_refuses_writes_even_without_the_checker(sql, tmp_path):
    db = tmp_path / "shop.sqlite"
    shutil.copy(shop.DB_PATH, db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.Error):
        guard.run(sql, db)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_row_limit_and_timeout():
    r = guard.run("SELECT * FROM order_items", max_rows=10)
    assert len(r.rows) == 10 and r.truncated
    endless = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    assert guard.check(endless).ok
    with pytest.raises(sqlite3.OperationalError, match="stopped after"):
        guard.run(endless, timeout_s=0.2)


def test_shipped_database_matches_the_builder(tmp_path):
    fresh = tmp_path / "shop.sqlite"
    shop.build(fresh)
    dump = lambda p: list(sqlite3.connect(p).iterdump())  # noqa: E731
    assert dump(fresh) == dump(shop.DB_PATH)
