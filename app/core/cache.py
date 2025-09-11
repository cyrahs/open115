from __future__ import annotations

import time
import threading
from typing import Any, Optional

from app.core import logger

log = logger.get("core.cache")


class _TTLCache:
    """A very small thread-safe TTL cache.

    - Keys are strings
    - Values are Any
    - TTL is provided in seconds per set()
    - Expired entries are lazily purged on access
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            value, expires_at = item
            if now >= expires_at:
                # expired; remove and miss
                try:
                    del self._store[key]
                except KeyError:  # pragma: no cover
                    pass
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        expires_at = time.time() + ttl_seconds
        with self._lock:
            self._store[key] = (value, expires_at)

    def purge_expired(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            for k in list(self._store.keys()):
                _, exp = self._store.get(k, (None, 0))
                if now >= exp:
                    self._store.pop(k, None)
                    removed += 1
        if removed:
            log.debug("Purged %d expired cache entries", removed)
        return removed


# Module-level singleton for convenience
ttl_cache = _TTLCache()

