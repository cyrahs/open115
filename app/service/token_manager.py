from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import asynccontextmanager
from typing import Optional

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore

from app.core import logger
from app.service import cloudflare, open115
from app.service.token_store import TokenRecord, token_store

log = logger.get("token_manager")

LOCK_PATH = os.getenv("OPEN115_TOKEN_MANAGER_LOCK", "/tmp/open115-token-manager.lock")
REFRESH_THRESHOLD_SECONDS = int(os.getenv("OPEN115_REFRESH_THRESHOLD", "900"))
SLEEP_MIN_SECONDS = 5


@asynccontextmanager
async def _acquire_manager_lock():
    if fcntl is None:  # pragma: no cover - Windows fallback
        yield None
        return

    loop = asyncio.get_running_loop()
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            await loop.run_in_executor(None, fcntl.flock, fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            log.info("Token manager lock already held; assuming another manager is running")
            os.close(fd)
            raise RuntimeError("manager-already-running")
        try:
            yield fd
        finally:
            if acquired:
                try:
                    await loop.run_in_executor(None, fcntl.flock, fd, fcntl.LOCK_UN)
                except OSError:  # pragma: no cover
                    pass
    finally:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass


async def _bootstrap_tokens() -> TokenRecord:
    record = token_store.get_tokens()
    if record and record.seconds_until_expiry() > REFRESH_THRESHOLD_SECONDS:
        log.info("Using existing tokens from local store; expires_at=%s", record.expires_at)
        return record

    log.info("Bootstrapping tokens from Cloudflare KV")
    access, refresh, expires_at = await cloudflare.fetch_tokens()
    token_store.set_tokens(access, refresh, expires_at)
    return token_store.get_tokens()  # type: ignore[return-value]


async def _persist_tokens_to_kv(access: str, refresh: str, expires_at: int) -> None:
    try:
        await cloudflare.persist_tokens(access, refresh, expires_at)
    except Exception as exc:  # pragma: no cover - defensive logging
        log.exception("Failed to persist tokens to KV: %s", exc)


async def _refresh_cycle(stop_event: asyncio.Event) -> None:
    record = await _bootstrap_tokens()
    while not stop_event.is_set():
        seconds_until_refresh = max(
            SLEEP_MIN_SECONDS,
            record.expires_at - REFRESH_THRESHOLD_SECONDS - time.time(),
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds_until_refresh)
            break
        except asyncio.TimeoutError:
            pass

        record = token_store.get_tokens()
        if not record:
            record = await _bootstrap_tokens()
            continue

        if record.seconds_until_expiry() > REFRESH_THRESHOLD_SECONDS:
            continue

        try:
            access, refresh, expires_at = await cloudflare.refresh_access_token(record.refresh_token)
        except Exception as exc:  # pragma: no cover - log and retry
            log.exception("Token refresh failed; retrying soon: %s", exc)
            await asyncio.sleep(SLEEP_MIN_SECONDS)
            continue

        token_store.set_tokens(access, refresh, expires_at)
        asyncio.create_task(_persist_tokens_to_kv(access, refresh, expires_at))
        record = TokenRecord(access, refresh, expires_at, time.time())


async def main() -> None:
    stop_event = asyncio.Event()

    try:
        async with _acquire_manager_lock():
            log.info("Starting Open115 token manager")

            loop = asyncio.get_running_loop()

            def _signal_handler(*_: object) -> None:
                stop_event.set()

            for sig_name in ("SIGTERM", "SIGINT"):
                if hasattr(signal, sig_name):
                    loop.add_signal_handler(getattr(signal, sig_name), _signal_handler)

            try:
                await _refresh_cycle(stop_event)
            finally:
                stop_event.set()
    except RuntimeError as exc:
        if str(exc) == "manager-already-running":
            return
        raise


if __name__ == "__main__":
    asyncio.run(main())
