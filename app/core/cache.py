from __future__ import annotations

import os
import pickle
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.core import logger

log = logger.get("core.cache")


def _resolve_db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env_path = os.getenv("OPEN115_CACHE_DB")
    if env_path:
        return Path(env_path)
    return Path(os.getenv("TMPDIR", "/tmp")) / "open115-cache.sqlite3"


class SQLiteTTLCache:
    """Process-safe TTL cache backed by SQLite.

    Values are pickled before storage to preserve types (e.g. Pydantic models).
    The same database file can be shared across multiple uvicorn workers.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = _resolve_db_path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            timeout=10,
            check_same_thread=False,
            isolation_level=None,  # autocommit mode
        )
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value BLOB NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        value_blob, expires_at = row
        if now >= expires_at:
            self.delete(key)
            return None
        try:
            return pickle.loads(value_blob)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Failed to unpickle cache value for key=%s: %s", key, exc)
            self.delete(key)
            return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            self.delete(key)
            return
        expires_at = time.time() + ttl_seconds
        blob = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cache(key, value, expires_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    expires_at=excluded.expires_at
                """,
                (key, sqlite3.Binary(blob), expires_at),
            )

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    def purge_expired(self) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM cache WHERE expires_at <= ?", (now,)
            )
            removed = cur.rowcount or 0
        if removed:
            log.debug("Purged %d expired cache entries", removed)
        return removed

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cache")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# Module-level singleton for convenience/shared usage
ttl_cache = SQLiteTTLCache()

__all__ = ["SQLiteTTLCache", "ttl_cache"]
