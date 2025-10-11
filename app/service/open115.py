from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Optional

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core import logger
from app.service.token_store import TokenRecord, token_store

log = logger.get("open115_service")

_client_lock = asyncio.Lock()
_client: Optional[httpx.AsyncClient] = None

HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)

_cache_lock = threading.RLock()
_cached_record: Optional[TokenRecord] = None
_REFRESH_THRESHOLD_SECONDS = int(os.getenv("OPEN115_REFRESH_THRESHOLD", "900"))

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


async def _close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ensure_tokens_ready(timeout: float = 30.0, poll_interval: float = 0.25) -> None:
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, token_store.wait_for_tokens, timeout, poll_interval)
    except TimeoutError as exc:  # pragma: no cover - defensive
        raise RuntimeError("115 tokens are not available; token manager not running?") from exc
    _refresh_cache(force=True)


def _refresh_cache(force: bool = False) -> TokenRecord:
    global _cached_record
    now = time.time()
    with _cache_lock:
        if not force and _cached_record is not None:
            # Reuse the cached token while we're safely outside the refresh window.
            if _cached_record.expires_at > now + _REFRESH_THRESHOLD_SECONDS:
                return _cached_record

        record = token_store.get_tokens()
        if not record:
            raise RuntimeError("115 tokens are not initialised in the token store")
        _cached_record = record
        return record


def get_access_token() -> str:
    return _refresh_cache(force=False).access_token


def clear_token_cache() -> None:
    global _cached_record
    with _cache_lock:
        _cached_record = None


async def add_magnets(magnets: list[str], task_path_id: str) -> dict[str, list[str]]:
    url = "https://proapi.115.com/open/offline/add_task_urls"

    async for attempt in _retryer():
        with attempt:
            client = await _get_client()
            headers = {
                "Authorization": f"Bearer {get_access_token()}",
            }
            body = {
                "urls": "\n".join(magnets),
                "wp_path_id": task_path_id,
            }
            res = await client.post(url, headers=headers, data=body)
            res.raise_for_status()
            return res.json()


async def get_file_info_by_path(path: str) -> dict:
    url = "https://proapi.115.com/open/folder/get_info"

    async for attempt in _retryer():
        with attempt:
            client = await _get_client()
            headers = {
                "Authorization": f"Bearer {get_access_token()}",
            }
            body = {
                "path": path,
            }
            res = await client.post(url, headers=headers, data=body)
            res.raise_for_status()
            return res.json()


async def get_download_url_by_pick_code(
    pick_code: str, ua: str | None = None
) -> dict:
    url = "https://proapi.115.com/open/ufile/downurl"

    async for attempt in _retryer():
        with attempt:
            client = await _get_client()
            headers = {
                "Authorization": f"Bearer {get_access_token()}",
            }
            if ua:
                headers["User-Agent"] = ua
            body = {
                "pick_code": pick_code,
            }
            res = await client.post(url, headers=headers, data=body)
            res.raise_for_status()
            return res.json()


async def get_play_url_by_pick_code(pick_code: str, ua: str | None = None) -> dict:
    url = "https://proapi.115.com/open/video/play"

    async for attempt in _retryer():
        with attempt:
            client = await _get_client()
            headers = {
                "Authorization": f"Bearer {get_access_token()}",
            }
            if ua:
                headers["User-Agent"] = ua
            params = {
                "pick_code": pick_code,
            }
            res = await client.get(url, headers=headers, params=params)
            res.raise_for_status()
            return res.json()


async def shutdown() -> None:
    await _close_client()
