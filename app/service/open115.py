import time
import threading
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core import logger, config

log = logger.get("open115_service")


class DuplicateError(Exception):
    pass

class TokenRefreshedError(Exception):
    pass


HTTP_NOT_FOUND = 404

# In-memory token state and background refresher management
_token_lock = threading.RLock()
_access_token: Optional[str] = None
_expires_at: Optional[int] = None
_bg_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None


def _set_tokens(token: str, expires_at: int) -> None:
    global _access_token, _expires_at
    with _token_lock:
        _access_token = token
        _expires_at = int(expires_at)


def get_access_token() -> str:
    with _token_lock:
        if not _access_token:
            raise RuntimeError(
                "115 tokens are not initialized. Call init_tokens() at app startup."
            )
        return _access_token


def init_tokens() -> None:
    """Fetch access token and expiry from KV; call at server startup."""
    token = get_from_kv("115_access_token")
    expires = int(get_from_kv("115_access_token_expires_at"))
    _set_tokens(token, expires)
    log.info("Loaded 115 access token; expires_at=%s", expires)


def refresh_if_necessary(threshold_seconds: int = 900) -> bool:
    """Refresh the access token if it will expire within threshold_seconds.

    Returns True if a refresh occurred.
    """
    with _token_lock:
        expires = _expires_at
    now = time.time()
    if expires is None:
        # Not initialized; load now
        init_tokens()
        return False
    if now + threshold_seconds > expires:
        refresh_access_token()
        return True
    return False


def start_background_token_refresher(
    sleep_seconds: int = 600, threshold_seconds: int = 900
) -> None:
    """Start a daemon thread that checks token expiry every sleep_seconds."""
    global _bg_thread, _stop_event
    if _bg_thread and _bg_thread.is_alive():
        return
    _stop_event = threading.Event()

    def _loop() -> None:
        while not _stop_event.is_set():
            try:
                refreshed = refresh_if_necessary(threshold_seconds)
                if refreshed:
                    log.info("Refreshed 115 access token in background")
            except Exception as e:
                log.exception("Background token refresh failed: %s", e)
            _stop_event.wait(sleep_seconds)

    _bg_thread = threading.Thread(
        target=_loop, name="open115-token-refresher", daemon=True
    )
    _bg_thread.start()


def stop_background_token_refresher() -> None:
    global _bg_thread, _stop_event
    if _stop_event:
        _stop_event.set()
    if _bg_thread and _bg_thread.is_alive():
        _bg_thread.join(timeout=1)
    _bg_thread = None
    _stop_event = None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)
def get_from_kv(key: str) -> str:
    """Get 115 refresh token and access token from Cloudflare KV.

    Returns:
        tuple[str, str]: A tuple containing (refresh_token, access_token)

    Raises:
        ValueError: If tokens cannot be retrieved from KV
    """
    url = f"https://api.cloudflare.com/client/v4/accounts/{config.cf_account_id}/storage/kv/namespaces/{config.cf_kv_id}/values/{key}"
    headers = {
        "Authorization": f"Bearer {config.cf_api_token}",
        "Content-Type": "application/json",
    }

    res = httpx.get(url, headers=headers, timeout=10)
    res.raise_for_status()

    if res.status_code == HTTP_NOT_FOUND:
        msg = f"{key} not found in Cloudflare KV"
        raise ValueError(msg)

    return res.text.strip('"')


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)
def put_to_kv(key: str, value: str) -> None:
    url = f"https://api.cloudflare.com/client/v4/accounts/{config.cf_account_id}/storage/kv/namespaces/{config.cf_kv_id}/values/{key}"
    headers = {
        "Authorization": f"Bearer {config.cf_api_token}",
        "Content-Type": "application/json",
    }
    res = httpx.put(url, headers=headers, data=value, timeout=10)
    res.raise_for_status()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)
def refresh_access_token() -> tuple[str, int]:
    url = "https://passportapi.115.com/open/refreshToken"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    payload = {
        "refresh_token": get_from_kv("115_refresh_token"),
    }
    log.info("Refreshing 115 access token")
    res = httpx.post(url, headers=headers, data=payload, timeout=10)
    res.raise_for_status()
    resj = res.json()
    if not bool(resj.get("state")):
        msg = f"Failed to refresh 115 access token: {resj}"
        raise TokenRefreshedError(msg)
    data = resj["data"]
    expires_in = int(data["expires_in"])
    expires_at = int(time.time() + expires_in)
    access_token = data["access_token"]
    # Persist to KV
    put_to_kv("115_access_token", access_token)
    put_to_kv("115_refresh_token", data["refresh_token"])
    put_to_kv("115_access_token_expires_at", str(expires_at))
    # Update in-memory state
    _set_tokens(access_token, expires_at)
    log.info("Successfully refreshed 115 access token")
    return access_token, expires_at


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)
def add_magnets(magnets: list[str], task_path_id: str) -> dict[str, list[str]]:
    url = "https://proapi.115.com/open/offline/add_task_urls"
    headers = {
        "Authorization": f"Bearer {get_access_token()}",
    }
    body = {
        "urls": "\n".join(magnets),
        "wp_path_id": task_path_id,
    }
    res = httpx.post(url, headers=headers, data=body, timeout=10)
    res.raise_for_status()
    return res.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)
def get_file_info_by_path(path: str) -> dict:
    url = "https://proapi.115.com/open/folder/get_info"
    headers = {
        "Authorization": f"Bearer {get_access_token()}",
    }
    body = {
        "path": path,
    }
    res = httpx.post(url, headers=headers, data=body, timeout=5)
    res.raise_for_status()
    return res.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)
def get_download_url_by_pick_code(pick_code: str, ua: str = None) -> str:
    url = "https://proapi.115.com/open/ufile/downurl"
    headers = {
        "Authorization": f"Bearer {get_access_token()}",
        "User-Agent": ua,
    }
    body = {
        "pick_code": pick_code,
    }
    res = httpx.post(url, headers=headers, data=body, timeout=5)
    res.raise_for_status()
    return res.json()