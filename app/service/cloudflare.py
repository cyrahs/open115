from __future__ import annotations

import asyncio
import time
from typing import Tuple

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core import config, logger

log = logger.get("cloudflare")

HTTP_NOT_FOUND = 404

_client_lock = asyncio.Lock()
_client: httpx.AsyncClient | None = None

HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
HTTP_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)

_RETRY_KWARGS = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True,
)


def _retryer() -> AsyncRetrying:
    return AsyncRetrying(**_RETRY_KWARGS)


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    http2=True,
                    timeout=HTTP_TIMEOUT,
                    limits=HTTP_LIMITS,
                )
    return _client


async def get_kv_value(key: str) -> str:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{config.cf_account_id}"
        f"/storage/kv/namespaces/{config.cf_kv_id}/values/{key}"
    )
    headers = {
        "Authorization": f"Bearer {config.cf_api_token}",
        "Content-Type": "application/json",
    }

    async for attempt in _retryer():
        with attempt:
            client = await _get_client()
            res = await client.get(url, headers=headers)
            if res.status_code == HTTP_NOT_FOUND:
                msg = f"{key} not found in Cloudflare KV"
                raise ValueError(msg)
            res.raise_for_status()
            return res.text.strip('"')


async def put_kv_value(key: str, value: str) -> None:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{config.cf_account_id}"
        f"/storage/kv/namespaces/{config.cf_kv_id}/values/{key}"
    )
    headers = {
        "Authorization": f"Bearer {config.cf_api_token}",
        "Content-Type": "application/json",
    }

    async for attempt in _retryer():
        with attempt:
            client = await _get_client()
            res = await client.put(url, headers=headers, data=value)
            res.raise_for_status()


async def fetch_tokens() -> Tuple[str, str, int]:
    access_token, refresh_token, expires_at = await asyncio.gather(
        get_kv_value("115_access_token"),
        get_kv_value("115_refresh_token"),
        get_kv_value("115_access_token_expires_at"),
    )
    return access_token, refresh_token, int(expires_at)


async def persist_tokens(access_token: str, refresh_token: str, expires_at: int) -> None:
    await asyncio.gather(
        put_kv_value("115_access_token", access_token),
        put_kv_value("115_refresh_token", refresh_token),
        put_kv_value("115_access_token_expires_at", str(expires_at)),
    )


async def refresh_access_token(refresh_token: str) -> Tuple[str, str, int]:
    url = "https://passportapi.115.com/open/refreshToken"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = {
        "refresh_token": refresh_token,
    }
    log.info("Refreshing 115 access token via Cloudflare client")

    async for attempt in _retryer():
        with attempt:
            client = await _get_client()
            res = await client.post(url, headers=headers, data=payload)
            res.raise_for_status()
            resj = res.json()

            if not bool(resj.get("state")):
                msg = f"Failed to refresh 115 access token: {resj}"
                raise RuntimeError(msg)

            data = resj["data"]
            expires_in = int(data["expires_in"])
            expires_at = int(time.time() + expires_in)
            access_token = data["access_token"]
            new_refresh_token = data["refresh_token"]
            log.info("Successfully refreshed 115 access token; expires_in=%s", expires_in)
            return access_token, new_refresh_token, expires_at
