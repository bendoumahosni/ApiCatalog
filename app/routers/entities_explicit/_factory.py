"""Shared CRUD route factory for the explicit per-entity routers.

This module is **not** itself mounted as a router. Each of the 7 files in this
package (``service_catalog.py``, ``service_category.py``,
``service_candidate.py``, ``generic_cfs.py``, ``concret_cfs.py``,
``concret_rfs.py``, ``service_profile.py``) is a thin, readable file that
declares one ``ExplicitEntityRoute`` and calls :func:`build_entity_router`
to get back a fully-wired ``APIRouter`` with all 5 CRUD operations
(``GET`` list, ``GET`` one, ``POST``, ``PUT``, ``DELETE``).

Keeping the registration logic here, factored once, means the 7 entities are
guaranteed to behave identically (same validation pipeline, same error
handling, same response shapes) while each entity still gets its own file to
read, edit, or extend independently — e.g. to later add an entity-specific
extra endpoint without touching the other 6.

Route ordering matters. FastAPI/Starlette match routes in declaration order,
and ``GET /{segment}/{entity_id}`` is a catch-all for that segment: anything
that looks like ``/{segment}/<anything>`` matches it, including a literal
sibling such as ``/{segment}/filter``. If a route like that is declared on a
*different* router that happens to be ``include_router``-ed *after* this one,
it is still permanently shadowed — Starlette resolves against the full,
flattened route table in registration order across all included routers, not
router-by-router. Concretely: a request for ``GET /concret-cfs/filter`` would
be swallowed by this factory's ``GET /concret-cfs/{entity_id}`` (with
``entity_id="filter"``) before it ever reaches a same-path-prefix route
declared elsewhere, even one declared "more specifically". The only reliable
fix is to register any literal sibling path on *this* router, *before*
calling :func:`build_entity_router` would register the catch-all — which is
exactly what ``register_before`` is for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, Query

from app.config import EntityType, Settings
from app.deps import get_converter, get_ingestion_service, settings_dep
from app.converter.ttl_to_json import graph_to_jsonld
from app.models.schemas import IngestResult

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "examples" / "schemas"


@dataclass(frozen=True)
class ExplicitEntityRoute:
    segment: str            # URL path segment, e.g. "service-catalog"
    entity_type: EntityType
    rdf_class: str           # ontology class local name, e.g. "ServiceCatalog"
    display_name: str        # human-readable name used in summaries
    schema_file: str         # filename under examples/schemas/
    # True for ServiceSpecification-derived entities (generic/concrete CFSS,
    # RFSS, profile), whose URI is minted from the raw `id` alone with no
    # type prefix (see JsonToGraphConverter.spec_uri). The bundled example's
    # placeholder id ("String") collides across these 4 entities if posted
    # as-is to more than one of them.
    id_only_uri: bool = False
    # Optional hook to register extra literal-path routes (e.g.
    # "/concret-cfs/filter", "/concret-cfs/search") on THIS router before the
    # generic "/{segment}/{entity_id}" catch-all is registered. Receives the
    # router and must register routes on it directly (e.g. via
    # @router.get(f"/{segment}/filter")) — see ``concret_cfs.py`` for the
    # concrete usage. See the module docstring for why this ordering is
    # mandatory, not cosmetic.
    register_before: Optional[Callable[[APIRouter], None]] = None


def load_example(filename: str) -> dict[str, Any]:
    return json.loads((_SCHEMAS_DIR / filename).read_text())


def build_entity_router(route_spec: ExplicitEntityRoute) -> APIRouter:
    """Build an APIRouter exposing the 5 CRUD endpoints for one entity type."""
    router = APIRouter(tags=[route_spec.display_name])

    segment = route_spec.segment
    entity_type = route_spec.entity_type
    rdf_class = route_spec.rdf_class
    name = route_spec.display_name
    example_payload = load_example(route_spec.schema_file)

    id_warning = (
        "\n\n**Note:** The id must be unique"
        if route_spec.id_only_uri else ""
    )

    # MUST run before "/{segment}/{entity_id}" is registered below — see the
    # module docstring's "Route ordering matters" section.
    if route_spec.register_before is not None:
        route_spec.register_before(router)

    @router.get(
        f"/{segment}",
        summary=f"List {name} entities",
        description=f"Lists every {name} instance stored in its repository.",
        operation_id=f"list_{entity_type.value}",
    )
    async def list_entities(
        limit: int = Query(100, ge=1, le=1000),
        settings: Settings = Depends(settings_dep),
    ) -> dict[str, Any]:
        repo = settings.repository_for(entity_type)
        rows = await get_ingestion_service().list_entities(repo, rdf_class, limit)
        return {"count": len(rows), "repository": repo, "results": rows}

    @router.get(
        f"/{segment}/{{entity_id}}",
        summary=f"Read one {name} as JSON-LD",
        description=f"Fetches a single {name} instance by id.",
        operation_id=f"get_{entity_type.value}",
    )
    async def get_entity(
        entity_id: str,
        settings: Settings = Depends(settings_dep),
    ) -> dict[str, Any]:
        repo = settings.repository_for(entity_type)
        uri = str(get_converter().top_uri(entity_type, entity_id))
        graph = await get_ingestion_service().get_named_graph(repo, uri)
        return {"entity_uri": uri, "repository": repo, "jsonld": graph_to_jsonld(graph)}

    @router.post(
        f"/{segment}",
        response_model=IngestResult,
        status_code=201,
        summary=f"Create a {name}",
        description=(
            f"Validates and stores a new {name}. "
            f"{id_warning}"
        ),
        operation_id=f"create_{entity_type.value}",
    )
    async def create_entity(
        payload: dict[str, Any] = Body(..., openapi_examples={
            segment: {"summary": f"{name} example", "value": example_payload},
        }),
    ) -> IngestResult:
        return await get_ingestion_service().ingest(
            payload, replace=False, expected_entity_type=entity_type,
        )

    @router.put(
        f"/{segment}/{{entity_id}}",
        response_model=IngestResult,
        summary=f"Create or replace a {name}",
        description=(
            f"Drops and re-inserts the named graph for this {name}. Behaves as "
            f"create if it does not exist yet."
        ),
        operation_id=f"put_{entity_type.value}",
    )
    async def put_entity(
        entity_id: str,
        payload: dict[str, Any] = Body(..., openapi_examples={
            segment: {"summary": f"{name} example", "value": example_payload},
        }),
    ) -> IngestResult:
        return await get_ingestion_service().ingest(
            payload, replace=True, expected_entity_type=entity_type,
        )

    @router.delete(
        f"/{segment}/{{entity_id}}",
        status_code=204,
        summary=f"Delete a {name}",
        description=f"Deletes the {name}.",
        operation_id=f"delete_{entity_type.value}",
    )
    async def delete_entity(
        entity_id: str,
        settings: Settings = Depends(settings_dep),
    ) -> None:
        repo = settings.repository_for(entity_type)
        uri = str(get_converter().top_uri(entity_type, entity_id))
        await get_ingestion_service().delete_named_graph(repo, uri)

    return router
