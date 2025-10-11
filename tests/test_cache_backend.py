from __future__ import annotations

from app.core import SQLiteTTLCache


def test_sqlite_cache_shares_state_across_instances(tmp_path):
    db_path = tmp_path / "shared-cache.sqlite"
    cache_a = SQLiteTTLCache(db_path)
    cache_b = SQLiteTTLCache(db_path)

    cache_a.set("greeting", {"text": "hello"}, ttl_seconds=60)
    assert cache_b.get("greeting") == {"text": "hello"}

    cache_b.set("farewell", "bye", ttl_seconds=60)
    assert cache_a.get("farewell") == "bye"

    cache_a.clear()
    assert cache_b.get("greeting") is None

    cache_a.close()
    cache_b.close()
