"""Explicit CRUD routes for ServiceCategory.

    GET    /service-category              list all Category instances
    GET    /service-category/{entity_id}  read one as JSON-LD
    POST   /service-category              create (validates + stores)
    PUT    /service-category/{entity_id}  create or replace (idempotent)
    DELETE /service-category/{entity_id}  delete and its subtree

Isolated in its own named graph (entity URI = named graph), in the same
repository as Catalog, Candidate, and ConcreteCFSS, so the typical
discovery query stays a plain SPARQL query with no federation.
"""

from __future__ import annotations

from app.config import EntityType
from app.routers.entities_explicit._factory import ExplicitEntityRoute, build_entity_router

ROUTE_SPEC = ExplicitEntityRoute(
    segment="service-category",
    entity_type=EntityType.SERVICE_CATEGORY,
    rdf_class="Category",
    display_name="Service Category",
    schema_file="service_category.schema.json",
)
router = build_entity_router(ROUTE_SPEC)
