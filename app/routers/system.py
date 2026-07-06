"""System / cross-cutting endpoints: validate, free SPARQL query."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Depends, Query

from app.config import EntityType, Settings
from app.deps import get_ingestion_service, settings_dep
from app.graphdb.client import get_client
from app.models.schemas import ValidationReport

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness probe (does not touch GraphDB)")
async def health() -> dict[str, str]:
    """Cheap liveness check: confirms the API process is up and serving.

    Deliberately does NOT call GraphDB, so a transient GraphDB outage does not
    make the orchestrator kill an otherwise-healthy API container. Used as the
    container HEALTHCHECK. For dependency readiness, see ``/ready``.
    """
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe (checks GraphDB connectivity)")
async def ready(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """Readiness check: the API can reach its GraphDB repository.

    Returns 200 with ``ready: true`` when the repository answers, 503 with
    ``ready: false`` otherwise — suitable for a Kubernetes readiness probe or a
    load balancer that should stop routing traffic while GraphDB is down.
    """
    reachable = await get_client().repository_health(settings.repository)
    if not reachable:
        raise HTTPException(
            status_code=503,
            detail={"ready": False, "repository": settings.repository,
                    "graphdb_base_url": settings.graphdb_base_url},
        )
    return {"ready": True, "repository": settings.repository}


@router.post("/validate", response_model=ValidationReport,
             summary="Validate a JSON document without writing")
async def validate(
    payload: dict[str, Any] = Body(...),
    expected_entity_type: Optional[EntityType] = Query(
        None,
        description=(
            "If set, the document must be detected as this TMF type or "
            "validation fails immediately with an entity_type_mismatch error. "
            "Used by entity-specific UI flows (e.g. the Ingestion page, once "
            "an entity is selected) that want validation to be as explicit as "
            "the corresponding POST route. Omit for free-form validation that "
            "auto-detects the type from the document itself."
        ),
    ),
) -> ValidationReport:
    service = get_ingestion_service()
    return await service.dry_run(payload, expected_entity_type=expected_entity_type)


@router.post("/query", summary="Run a free SPARQL query against one repository")
async def free_query(
    repository: str = Body(..., embed=True),
    query: str = Body(..., embed=True),
    settings: Settings = Depends(settings_dep),
) -> dict[str, Any]:
    if repository not in settings.all_repositories:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown repository '{repository}'. Known: {settings.all_repositories}",
        )
    rows = await get_client().select(repository, query)
    return {"count": len(rows), "results": rows}
