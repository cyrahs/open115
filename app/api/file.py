from __future__ import annotations

from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ValidationError

from app.core import config, logger, ttl_cache

log = logger.get("api.file")
router = APIRouter(prefix="/file", tags=["file"])


class FileInfoResponse(BaseModel):
    state: bool
    message: str
    code: int
    data: FileInfo | list


class FileInfo(BaseModel):
    count: int
    size: str
    size_byte: int
    folder_count: int
    play_long: int
    show_play_long: int
    ptime: str
    utime: str
    file_name: str
    pick_code: str
    sha1: str
    file_id: str
    is_mark: str
    open_time: int
    file_category: str
    paths: list[PathInfo]


class PathInfo(BaseModel):
    file_id: str
    file_name: str
    iss: str | None = None


def _download_cache_key(path: str, ua: str) -> str:
    raw_key = f"download:{path}|{ua}"
    return sha256(raw_key.encode("utf-8")).hexdigest()


def _play_cache_key(path: str) -> str:
    raw_key = f"play:{path}"
    return sha256(raw_key.encode("utf-8")).hexdigest()


def _file_info_cache_key(path: str) -> str:
    raw_key = f"file-info:{path}"
    return sha256(raw_key.encode("utf-8")).hexdigest()


@router.get("/info")
async def get_file_info(path: str) -> FileInfo:
    """Get file/folder info by path from 115 service."""
    try:
        from app.service import open115 as svc
    except Exception as e:  # pragma: no cover
        log.exception("Failed to import app.service.open115: %s", e)
        raise HTTPException(status_code=500, detail="Service unavailable") from e

    try:
        res = await svc.get_file_info_by_path(path)
    except Exception as e:
        log.error("Failed to get file info from upstream: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    try:
        res = FileInfoResponse.model_validate(res)
    except ValidationError as e:
        log.exception(
            "Failed to get file info (upstream response validation error): %s", e
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Invalid upstream response: {e}",
                "origin_response": res,
            },
        ) from e
    if res.state is False:
        log.error(
            "Failed to get file info for path=%s: error from 115: %s", path, res.message
        )
        raise HTTPException(status_code=500, detail=res.message)
    if res.data == []:
        log.error("No file found for path=%s", path)
        raise HTTPException(status_code=404, detail="File not found")
    return res.data


class DownloadUrlInfo(BaseModel):
    file_name: str
    file_size: int
    pick_code: str
    sha1: str
    url: DownloadUrl


class DownloadUrl(BaseModel):
    url: str


class DownloadUrlResponse(BaseModel):
    state: bool
    message: str
    code: int
    data: dict[str, DownloadUrlInfo] | list


async def _get_file_info_cached(path: str) -> FileInfo:
    cache_key = _file_info_cache_key(path)
    cached = ttl_cache.get(cache_key)
    if cached:
        return cached
    info = await get_file_info(path)
    ttl_cache.set(cache_key, info, config.link_cache_ttl_seconds)
    return info


async def _resolve_download_url(path: str, request: Request) -> str:
    """Resolve the direct download URL for a given path, with UA-aware caching.

    Returns the URL as a string. Raises HTTPException on error.
    """
    try:
        from app.service import open115 as svc
    except Exception as e:  # pragma: no cover
        log.exception("Failed to import app.service.open115: %s", e)
        raise HTTPException(status_code=500, detail="Service unavailable") from e

    # Build cache key from path and User-Agent
    ua = request.headers.get("User-Agent") or ""
    key = _download_cache_key(path, ua)

    # Check cache first
    cached = ttl_cache.get(key)
    if cached:
        log.info("Cache hit for download url for path %s", path)
        return cached

    info = await _get_file_info_cached(path)
    pick_code = info.pick_code
    try:
        result = await svc.get_download_url_by_pick_code(pick_code, ua=ua)
    except Exception as e:
        log.error("Failed to get download url from upstream: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    try:
        res = DownloadUrlResponse.model_validate(result)
    except ValidationError as e:
        log.exception(
            "Failed to get download url (upstream response validation error): %s", e
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Invalid upstream response: {e}",
                "origin_response": result,
            },
        ) from e
    res_data_key = list(res.data.keys())[0]
    download_url = res.data[res_data_key].url.url

    ttl_cache.set(key, download_url, config.link_cache_ttl_seconds)
    return download_url


@router.api_route("/download", methods=["GET", "HEAD"])
async def redirect_to_download_link(path: str, request: Request) -> RedirectResponse:
    """Get download url for a file by file id from 115 service and redirect to it.

    Adds a link cache keyed by a hash of request path and User-Agent.
    """
    download_url = await _resolve_download_url(path, request)
    log.info(f"Return download url for path {path}")
    return RedirectResponse(url=download_url, status_code=302)


class VideoUrlInfo(BaseModel):
    url: str
    height: int
    width: int
    definition: int
    title: str
    definition_n: int


class PlayUrlData(BaseModel):
    video_url: list[VideoUrlInfo]

class PlayUnavailable(BaseModel):
    video_push_state: bool

class PlayUrlResponse(BaseModel):
    state: bool
    message: str
    code: int
    data: PlayUrlData | PlayUnavailable | dict


@router.api_route("/play", methods=["GET", "HEAD"])
async def redirect_to_play_link(path: str, request: Request) -> RedirectResponse:
    """Get play url for a file by file id from 115 service and redirect to it.

    If the play URL is unavailable, fall back to the direct download URL.
    """
    try:
        from app.service import open115 as svc
    except Exception as e:  # pragma: no cover
        log.exception("Failed to import app.service.open115: %s", e)
        raise HTTPException(status_code=500, detail="Service unavailable") from e

    # try cache first (play cache is path-only)
    key = _play_cache_key(path)
    cached = ttl_cache.get(key)
    if cached:
        return RedirectResponse(url=cached, status_code=302)

    info = await _get_file_info_cached(path)
    pick_code = info.pick_code
    try:
        result = await svc.get_play_url_by_pick_code(pick_code)
    except Exception as e:
        log.error("Failed to get play url from upstream: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    try:
        res = PlayUrlResponse.model_validate(result)
    except ValidationError as e:
        log.exception(
            "Failed to get play url (upstream response validation error): %s", e
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Invalid upstream response: {e}",
                "origin_response": result,
            },
        ) from e

    # If play is unavailable -> fall back to direct download URL
    if isinstance(res.data, PlayUnavailable):
        log.info(f"Play unavailable for path {path}; falling back to download URL")
        download_url = await _resolve_download_url(path, request)
        # Cache play key with the download URL too, to speed up subsequent /play hits
        ttl_cache.set(key, download_url, config.link_cache_ttl_seconds)
        return RedirectResponse(url=download_url, status_code=302)

    # Otherwise, normal play flow
    video_url_info = res.data.video_url[-1]
    video_url = video_url_info.url
    if video_url.startswith("http://"):
        video_url = "https://" + video_url[len("http://") :]

    ttl_cache.set(key, video_url, config.link_cache_ttl_seconds)
    log.info(f"Return video url with tag {video_url_info.title} for path {path}")
    return RedirectResponse(url=video_url, status_code=302)
