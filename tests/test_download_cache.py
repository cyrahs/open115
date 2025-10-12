import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import file as file_api
from app.core import config, ttl_cache


@pytest.fixture(autouse=True)
def reset_cache_ttl(monkeypatch):
    # Ensure a known TTL for tests; default 30 minutes
    monkeypatch.setattr(config, "link_cache_ttl_seconds", 1800)
    yield


@pytest.fixture(autouse=True)
def clear_cache():
    ttl_cache.clear()
    yield
    ttl_cache.clear()


def make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(file_api.router)
    return app


def _mock_file_info(path: str) -> dict[str, Any]:
    # Minimal valid structure matching FileInfoResponse
    return {
        "state": True,
        "message": "ok",
        "code": 200,
        "data": {
            "count": 1,
            "size": "123",
            "size_byte": 123,
            "folder_count": 0,
            "play_long": 0,
            "show_play_long": 0,
            "ptime": "",
            "utime": "",
            "file_name": "test.bin",
            "pick_code": "PICKCODE",
            "sha1": "deadbeef",
            "file_id": "fid",
            "is_mark": "0",
            "open_time": 0,
            "file_category": "bin",
            "paths": [{"file_id": "fid", "file_name": "test.bin", "iss": None}],
        },
    }


def _mock_download_response(url: str) -> dict[str, Any]:
    return {
        "state": True,
        "message": "ok",
        "code": 200,
        "data": {
            "PICKCODE": {
                "file_name": "test.bin",
                "file_size": 123,
                "pick_code": "PICKCODE",
                "sha1": "deadbeef",
                "url": {"url": url},
            }
        },
    }


def test_download_uses_cache_on_second_request(monkeypatch):
    # Arrange
    app = make_test_app()
    client = TestClient(app, follow_redirects=False)

    # Counters to verify upstream calls
    counters = {"info": 0, "download": 0}

    from app.service import open115 as svc

    async def fake_get_file_info_by_path(path: str):
        counters["info"] += 1
        return _mock_file_info(path)

    async def fake_get_download_url_by_pick_code(pick_code: str, ua: str | None = None):
        counters["download"] += 1
        return _mock_download_response("https://example.com/file.bin")

    monkeypatch.setattr(svc, "get_file_info_by_path", fake_get_file_info_by_path)
    monkeypatch.setattr(svc, "get_download_url_by_pick_code", fake_get_download_url_by_pick_code)

    headers = {"User-Agent": "TestUA/1.0"}

    # Act - first request (cache miss)
    r1 = client.get("/file/download", params={"path": "/a/b.bin"}, headers=headers)
    # Assert
    assert r1.status_code == 302
    assert r1.headers["location"] == "https://example.com/file.bin"
    assert counters["info"] == 1
    assert counters["download"] == 1

    # Act - second request (should be cache hit; no extra upstream download call)
    r2 = client.get("/file/download", params={"path": "/a/b.bin"}, headers=headers)
    assert r2.status_code == 302
    assert r2.headers["location"] == "https://example.com/file.bin"
    assert counters["info"] == 1
    assert counters["download"] == 1


def test_download_cache_expires(monkeypatch):
    # Arrange - set a very small TTL: 2 seconds
    monkeypatch.setattr(config, "link_cache_ttl_seconds", 2)

    app = make_test_app()
    client = TestClient(app, follow_redirects=False)

    counters = {"download": 0}
    from app.service import open115 as svc

    async def fake_get_file_info_by_path(path: str):
        return _mock_file_info(path)

    async def fake_get_download_url_by_pick_code(pick_code: str, ua: str | None = None):
        counters["download"] += 1
        # Return a URL that encodes the count, to observe changes across calls
        return _mock_download_response(f"https://example.com/file-{counters['download']}.bin")

    monkeypatch.setattr(svc, "get_file_info_by_path", fake_get_file_info_by_path)
    monkeypatch.setattr(svc, "get_download_url_by_pick_code", fake_get_download_url_by_pick_code)

    headers = {"User-Agent": "TestUA/2.0"}

    # First call -> miss
    r1 = client.get("/file/download", params={"path": "/expire.bin"}, headers=headers)
    assert r1.status_code == 302
    assert r1.headers["location"].endswith("file-1.bin")

    # Immediate second call -> hit (same URL)
    r2 = client.get("/file/download", params={"path": "/expire.bin"}, headers=headers)
    assert r2.status_code == 302
    assert r2.headers["location"].endswith("file-1.bin")

    # After TTL -> should expire and fetch again
    time.sleep(3)
    r3 = client.get("/file/download", params={"path": "/expire.bin"}, headers=headers)
    assert r3.status_code == 302
    assert r3.headers["location"].endswith("file-2.bin")
