from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ValidationError

from app.core import logger

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


@router.get("/info")
async def get_file_info(path: str) -> FileInfo:
    """Get file/folder info by path from 115 service."""
    try:
        from app.service import open115 as svc
    except Exception as e:  # pragma: no cover
        log.exception("Failed to import app.service.open115: %s", e)
        raise HTTPException(status_code=500, detail="Service unavailable")

    try:
        res = svc.get_file_info_by_path(path)
    except Exception as e:
        log.error("Failed to get file info from upstream: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
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
        )
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


@router.api_route("/download", methods=["GET", "HEAD"])
async def redirect_to_download_link(path: str, request: Request) -> RedirectResponse:
    """Get download url for a file by file id from 115 service and redirect to it."""
    try:
        from app.service import open115 as svc
    except Exception as e:  # pragma: no cover
        log.exception("Failed to import app.service.open115: %s", e)
        raise HTTPException(status_code=500, detail="Service unavailable")

    info = await get_file_info(path)
    pick_code = info.pick_code
    try:
        result = svc.get_download_url_by_pick_code(
            pick_code, ua=request.headers.get("User-Agent")
        )
    except Exception as e:
        log.error("Failed to get download url from upstream: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
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
        )
    res_data_key = list(res.data.keys())[0]
    download_url = res.data[res_data_key].url.url
    return RedirectResponse(url=download_url, status_code=302)
