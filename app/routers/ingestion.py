"""Generic ingestion endpoint.

A single POST accepts any of the seven TMF document types. The type is detected
from the payload and the document is routed to the correct repository
automatically. This is the recommended entry point; the per-entity routes in
``entities.py`` exist for read/update/delete and for clients that prefer an
explicit resource-oriented surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from app.deps import get_ingestion_service
from app.models.schemas import IngestResult

router = APIRouter(tags=["ingestion"])


@router.post(
    "/ingest",
    response_model=IngestResult,
    status_code=201,
    summary="Ingest any TMF JSON document (auto-detect type, validate, store)",
)
async def ingest(payload: dict[str, Any] = Body(...)) -> IngestResult:
    return await get_ingestion_service().ingest(payload, replace=False)
