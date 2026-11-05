"""SQLite connection wrapper.

Concurrency model
-----------------
* ``journal_mode=WAL`` allows concurrent readers while a writer is active.
* All write operations run inside ``BEGIN IMMEDIATE`` so SQLite takes the
  write lock up front, giving us a serialized compare-and-set for the atomic
  release consumption primitive.
* Python's ``sqlite3.Connection`` is *not* safe for two threads to use the
  same connection for `BEGIN IMMEDIATE` at the same time: the second call
  fails with "cannot start a transaction within a transaction". We therefore
  serialize *transactions* at the application level with a ``threading.Lock``.
  The BEGIN IMMEDIATE is kept as defence in depth (and to preserve the same
  semantics if the lock were removed).
* Reads (``query``/``query_one``) are not locked. SQLite guarantees
  consistent reads under WAL, and we never read mid-write from the same
  connection because writes are serialized.

This is a *single-process* architecture. Multiple Python processes would
require a different store (e.g., PostgreSQL with ``SELECT ... FOR UPDATE``).
See ARCHITECTURE.md §"Single-process limitations".
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._tx_lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        self._conn.executescript(sql)

    @property
    def conn(self) -> sqlite3.Connection:
        """Read-only access. Callers must not START a transaction manually."""
        return self._conn

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._tx_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass