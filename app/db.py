"""SQLite 存储：建表自动在应用启动时执行。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DB_PATH = os.environ.get("SVGAPI_DB_PATH", "/data/svgapi.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paths (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    d               TEXT    NOT NULL,
    fill_rule       TEXT    NOT NULL,
    metadata        TEXT    NOT NULL DEFAULT '{}',
    normalized_d    TEXT,
    normalized_info TEXT,
    warnings        TEXT,
    contour_metadata TEXT   NOT NULL DEFAULT '[]',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """幂等建表，容器启动即可用。"""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
