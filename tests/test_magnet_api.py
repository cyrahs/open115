from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.magnet import router


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_add_magnets_categorizes_results(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.service import open115 as svc

    async def fake_add_magnets(magnets: list[str], dir_id: str) -> dict:
        return {
            "state": True,
            "message": "ok",
            "code": 200,
            "data": [
                {
                    "state": True,
                    "code": 200,
                    "message": "created",
                    "info_hash": "abc123",
                    "url": magnets[0],
                },
                {
                    "state": False,
                    "code": 10008,
                    "message": "duplicate",
                    "info_hash": None,
                    "url": magnets[1],
                },
                {
                    "state": False,
                    "code": 10010,
                    "message": "failed",
                    "info_hash": None,
                    "url": magnets[2],
                },
            ],
        }

    monkeypatch.setattr(svc, "add_magnets", fake_add_magnets)

    payload = {
        "magnets": [
            "magnet:?xt=urn:btih:SUCCESS",
            "magnet:?xt=urn:btih:DUPLICATE",
            "magnet:?xt=urn:btih:FAILED",
        ],
        "dir_id": "12345",
    }

    response = client.post("/magnet/add", json=payload)

    assert response.status_code == 200
    assert response.json() == [
        {
            "state": True,
            "code": 200,
            "message": "created",
            "info_hash": "abc123",
            "url": "magnet:?xt=urn:btih:SUCCESS",
            "type": "success",
        },
        {
            "state": False,
            "code": 10008,
            "message": "duplicate",
            "info_hash": None,
            "url": "magnet:?xt=urn:btih:DUPLICATE",
            "type": "duplicate",
        },
        {
            "state": False,
            "code": 10010,
            "message": "failed",
            "info_hash": None,
            "url": "magnet:?xt=urn:btih:FAILED",
            "type": "failed",
        },
    ]


def test_add_magnets_handles_upstream_error_state(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.service import open115 as svc

    async def fake_add_magnets(_: list[str], __: str) -> dict:
        return {
            "state": False,
            "message": "bad upstream",
            "code": 599,
            "data": [],
        }

    monkeypatch.setattr(svc, "add_magnets", fake_add_magnets)

    response = client.post(
        "/magnet/add",
        json={
            "magnets": ["magnet:?xt=urn:btih:ERROR"],
            "dir_id": "deadbeef",
        },
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "bad upstream"}


def test_add_magnets_invalid_upstream_schema(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.service import open115 as svc

    async def fake_add_magnets(_: list[str], __: str) -> dict:
        # Missing required fields to trigger validation failure
        return {
            "state": True,
            "message": "ok",
            "code": 200,
            "data": [{"message": "missing fields"}],
        }

    monkeypatch.setattr(svc, "add_magnets", fake_add_magnets)

    response = client.post(
        "/magnet/add",
        json={
            "magnets": ["magnet:?xt=urn:btih:BADSCHEMA"],
            "dir_id": "deadbeef",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["error"].startswith("Invalid upstream response")
    assert body["detail"]["origin_response"]["data"][0]["message"] == "missing fields"


def test_add_magnets_raises_when_service_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.service import open115 as svc

    async def fake_add_magnets(_: list[str], __: str) -> dict:
        raise RuntimeError("network down")

    monkeypatch.setattr(svc, "add_magnets", fake_add_magnets)

    response = client.post(
        "/magnet/add",
        json={
            "magnets": ["magnet:?xt=urn:btih:FAIL"],
            "dir_id": "deadbeef",
        },
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream request failed: network down"}
