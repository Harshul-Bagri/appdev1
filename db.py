import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"], isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    path = Path(__file__).with_name("schema.sql")
    get_db().executescript(path.read_text(encoding="utf-8"))


def rows(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def row(sql, params=()):
    return get_db().execute(sql, params).fetchone()


@contextmanager
def write_transaction():
    connection = get_db()
    # Lock before reading capacity or status so a second writer sees the committed change.
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
