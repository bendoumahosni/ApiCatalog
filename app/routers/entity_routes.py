"""Explicit, entity-specific CRUD routes.

For each of the 7 TMF entity types this module registers:

    GET    /<segment>              list entities of this type
    GET    /<segment>/{entity_id}  read one entity as JSON-LD
    POST   /<segment>              create (validates + stores)
    PUT    /<segment>/{entity_id}  create or replace (idempotent)
    DELETE /<segment>/{entity_id}  delete the entity and its subtree

Unlike the generic ``/{kind}`` routes in ``entities.py`` (kept internally as
the shared implementation, not removed), these routes are **explicit**: each
one is bound to exactly one ``EntityType`` at declaration time, not resolved
dynamically from a ``kind`` path parameter. POST/PUT additionally enforce that
a posted document's detected TMF type matches what the route expects
(``EntityTypeMismatchError`` otherwise) -- POSTing a ServiceCandidate document
to ``POST /service-catalog`` is rejected outright rather than silently
auto-routed elsewhere.

Each POST/PUT body carries a realistic, schema-conformant example in Swagger
(``openapi_examples``), sourced from ``examples/schemas/*.schema.json``, so a
user can send the example as-is and get a successful response (provided its
references, e.g. the generic CFSS a concrete spec specializes, already exist).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from app.config import EntityType, Settings
from app.deps import get_converter, get_ingestion_service, settings_dep
from app.converter.ttl_to_json import graph_to_jsonld
from app.models.schemas import IngestResult

router = APIRouter(tags=["entities (explicit)"])

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "schemas"


@dataclass(frozen=True)
class ExplicitEntityRoute:
    segment: str
    entity_type: EntityType
    rdf_class: str
    display_name: str
    schema_file: str
    # True for ServiceSpecification-derived entities (generic/concrete CFSS,
    # RFSS, profile), whose URI is minted from the raw `id` alone with no
    # type prefix (see JsonToGraphConverter.spec_uri). This means the bundled
    # example's placeholder id ("String") collides across these 4 entities if
    # posted as-is to more than one of them -- worth calling out explicitly
    # in the route description rather than leaving it as a silent trap.
    id_only_uri: bool = False


ROUTES: list[ExplicitEntityRoute] = [
    ExplicitEntityRoute("service-catalog", EntityType.SERVICE_CATALOG,
                        "ServiceCatalog", "Service Catalog", "service_catalog.schema.json"),
    ExplicitEntityRoute("service-category", EntityType.SERVICE_CATEGORY,
                        "Category", "Service Category", "service_category.schema.json"),
    ExplicitEntityRoute("service-candidate", EntityType.SERVICE_CANDIDATE,
                        "ServiceCandidate", "Service Candidate", "service_candidate.schema.json"),
    ExplicitEntityRoute("generic-cfs", EntityType.GENERIC_CFSS,
                        "GenericCFSS", "Generic CFSS", "generic_cfss.schema.json", id_only_uri=True),
    ExplicitEntityRoute("concret-cfs", EntityType.CONCRETE_CFSS,
                        "ConcreteCFSS", "Concrete CFSS", "concret_cfss.schema.json", id_only_uri=True),
    ExplicitEntityRoute("concret-rfs", EntityType.CONCRETE_RFSS,
                        "RFS", "RFSS", "rfss.schema.json", id_only_uri=True),
    ExplicitEntityRoute("service-profile", EntityType.PROFILE_MANAGEMENT,
                        "ServiceManagementProfile", "Service Management Profile",
                        "service_profile.schema.json", id_only_uri=True),
]


def _load_example(filename: str) -> dict[str, Any]:
    return json.loads((_SCHEMAS_DIR / filename).read_text())


def _register(route_spec: ExplicitEntityRoute) -> None:
    """Register the 5 CRUD endpoints for one entity type.

    A closure-based factory is used (rather than 7 copy-pasted route blocks)
    so the 7 entities share identical, tested behaviour; only the path,
    entity type, RDF class, and example payload differ per call.
    """
    segment = route_spec.segment
    entity_type = route_spec.entity_type
    rdf_class = route_spec.rdf_class
    name = route_spec.display_name
    example_payload = _load_example(route_spec.schema_file)

    id_warning = (
        "\n\n**Note on the example below:** this entity's URI is minted from "
        "its `id` field alone (no type prefix). The bundled example uses a "
        "literal placeholder id (`\"String\"`) to show the expected shape — "
        "replace it with a real, unique id before posting, otherwise this "
        "document will collide with any other generic CFSS / concrete CFSS / "
        "RFSS / management profile example also posted with the same "
        "placeholder."
        if route_spec.id_only_uri else ""
    )

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
        description=f"Fetches a single {name} instance (and its whole subtree) by id.",
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
            f"Validates (JSON contract + SHACL + cross-repository references) and "
            f"stores a new {name}. The posted document must be detected as "
            f"'{entity_type.value}' -- documents of any other TMF type are rejected."
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
        summary=f"Create or replace a {name} (idempotent)",
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
        description=f"Deletes the {name} and its whole subtree (DROP GRAPH).",
        operation_id=f"delete_{entity_type.value}",
    )
    async def delete_entity(
        entity_id: str,
        settings: Settings = Depends(settings_dep),
    ) -> None:
        repo = settings.repository_for(entity_type)
        uri = str(get_converter().top_uri(entity_type, entity_id))
        await get_ingestion_service().delete_named_graph(repo, uri)


for _route_spec in ROUTES:
    _register(_route_spec)
