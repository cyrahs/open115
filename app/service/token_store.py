from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core import logger

log = logger.get("token_store")


@dataclass(slots=True)
class TokenRecord:
    access_token: str
    refresh_token: str
    expires_at: int
    updated_at: float

    def seconds_until_expiry(self) -> float:
        return self.expires_at - time.time()


def _default_db_path() -> Path:
    env_path = os.getenv("OPEN115_TOKEN_DB")
    if env_path:
        return Path(env_path)
    base = os.getenv("TMPDIR", "/tmp")
    return Path(base) / "open115-tokens.sqlite3"


class TokenStore:
    """SQLite-backed token store shared across processes."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path else _default_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            timeout=10,
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def set_tokens(self, access: str, refresh: str, expires_at: int) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tokens(id, access_token, refresh_token, expires_at, updated_at)
                VALUES(1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (access, refresh, int(expires_at), now),
            )
        log.debug("Token store updated; expires_at=%s", expires_at)

    def get_tokens(self) -> Optional[TokenRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT access_token, refresh_token, expires_at, updated_at FROM tokens WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        access, refresh, expires_at, updated_at = row
        return TokenRecord(
            access_token=access,
            refresh_token=refresh,
            expires_at=int(expires_at),
            updated_at=float(updated_at),
        )

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tokens WHERE id = 1")

    def wait_for_tokens(self, timeout: float = 30.0, poll_interval: float = 0.25) -> TokenRecord:
        deadline = time.time() + timeout
        while True:
            record = self.get_tokens()
            if record:
                return record
            if time.time() >= deadline:
                raise TimeoutError("Token store not populated within timeout")
            time.sleep(poll_interval)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


token_store = TokenStore()

__all__ = ["TokenStore", "TokenRecord", "token_store"]
