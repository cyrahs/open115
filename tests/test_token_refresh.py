from __future__ import annotations

import asyncio
import time

import pytest

from app.service import cloudflare, open115 as svc
from app.service.token_store import token_store


@pytest.mark.asyncio
async def test_ensure_tokens_ready_waits_for_store(monkeypatch: pytest.MonkeyPatch):
    token_store.clear()

    async def populate_store():
        await asyncio.sleep(0.05)
        token_store.set_tokens("access", "refresh", int(time.time()) + 3600)

    asyncio.create_task(populate_store())

    await svc.ensure_tokens_ready(timeout=1.0, poll_interval=0.01)
    assert svc.get_access_token() == "access"


@pytest.mark.asyncio
async def test_fetch_tokens_from_kv(monkeypatch: pytest.MonkeyPatch):
    calls = {}

    async def fake_get_from_kv(key: str) -> str:
        calls[key] = calls.get(key, 0) + 1
        mapping = {
            "115_access_token": "acc",
            "115_refresh_token": "ref",
            "115_access_token_expires_at": "1234",
        }
        return mapping[key]

    monkeypatch.setattr(cloudflare, "get_kv_value", fake_get_from_kv)

    access, refresh, expires = await cloudflare.fetch_tokens()

    assert access == "acc"
    assert refresh == "ref"
    assert expires == 1234
    assert calls == {
        "115_access_token": 1,
        "115_refresh_token": 1,
        "115_access_token_expires_at": 1,
    }


@pytest.mark.asyncio
async def test_cached_token_reused_before_refresh_window(monkeypatch: pytest.MonkeyPatch):
    threshold = 900
    monkeypatch.setattr(svc, "_REFRESH_THRESHOLD_SECONDS", threshold)
    svc.clear_token_cache()

    expires_at = int(time.time()) + 3600
    token_store.set_tokens("cached-token", "refresh-token", expires_at)

    calls = {"count": 0}
    original_get = token_store.get_tokens

    def wrapped_get_tokens():
        calls["count"] += 1
        result = original_get()
        if result:
            return result
        raise AssertionError("Token store unexpectedly empty")

    monkeypatch.setattr(token_store, "get_tokens", wrapped_get_tokens)

    await svc.ensure_tokens_ready(timeout=1.0, poll_interval=0.01)
    assert calls["count"] == 2  # wait_for_tokens + forced refresh

    assert svc.get_access_token() == "cached-token"
    assert calls["count"] == 2  # cache hit, no extra DB read


@pytest.mark.asyncio
async def test_token_reloaded_when_within_refresh_window(monkeypatch: pytest.MonkeyPatch):
    threshold = 900
    monkeypatch.setattr(svc, "_REFRESH_THRESHOLD_SECONDS", threshold)
    svc.clear_token_cache()

    soon_expiring = int(time.time()) + threshold // 2
    token_store.set_tokens("old-token", "old-refresh", soon_expiring)

    calls = {"count": 0}
    original_get = token_store.get_tokens

    def wrapped_get_tokens():
        calls["count"] += 1
        return original_get()

    monkeypatch.setattr(token_store, "get_tokens", wrapped_get_tokens)

    await svc.ensure_tokens_ready(timeout=1.0, poll_interval=0.01)
    assert calls["count"] == 2  # wait_for_tokens + forced refresh

    token_store.set_tokens("new-token", "new-refresh", int(time.time()) + 3600)

    assert svc.get_access_token() == "new-token"
    assert calls["count"] == 3  # reload triggered due to refresh window
