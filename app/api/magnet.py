from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from typing import Literal

from app.core import logger

log = logger.get("api.magnet")
router = APIRouter(prefix="/magnet", tags=["file"])


# Request schema
class MagnetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    magnets: list[str] = Field(
        ..., min_length=1, description="List of magnet links starting with 'magnet:'"
    )
    dir_id: str = Field(..., description="115 dir id where tasks should be created")


# Upstream response schemas
class MagnetAddResult(BaseModel):
    state: bool
    code: int
    message: str
    info_hash: str | None = None
    url: str


class MagnetAddEnvelope(BaseModel):
    state: bool
    message: str
    code: int
    data: list[MagnetAddResult]


class MagnetAddResponse(MagnetAddResult):
    type: Literal["success", "duplicate", "failed"]


@router.post("/add")
async def add_magnets(payload: MagnetsRequest) -> list[MagnetAddResponse]:
    """Add offline download tasks by magnet links via 115 service."""
    try:
        from app.service import open115 as svc
    except Exception as e:  # pragma: no cover - import-time failure surfaced as 500
        log.exception("Failed to import app.service.open115: %s", e)
        raise HTTPException(status_code=500, detail="Service unavailable")

    try:
        resj = await svc.add_magnets(payload.magnets, payload.dir_id)
        envelope = MagnetAddEnvelope.model_validate(resj)
    except ValidationError as e:
        log.exception("Failed to add magnets (upstream response validation error): %s", e)
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Invalid upstream response: {e}",
                "origin_response": resj,
            }
        )
    except Exception as e:
        # Network-level or unexpected service errors or schema validation errors
        log.exception("Failed to add magnets (request/validation error): %s", e)
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")
    if envelope.state is False:
        log.error("Failed to add magnets (error from 115): %s", envelope.message)
        raise HTTPException(status_code=500, detail=envelope.message)
    response = []
    for result in envelope.data:
        result_dict = result.model_dump()
        if result.state:
            result_dict["type"] = "success"
        elif result.code == 10008:
            result_dict["type"] = "duplicate"
        else:
            result_dict["type"] = "failed"
        response.append(result_dict)
    return response
