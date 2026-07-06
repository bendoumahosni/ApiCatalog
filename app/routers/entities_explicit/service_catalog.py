"""Explicit CRUD routes for ServiceCatalog.

    GET    /service-catalog              list all ServiceCatalog instances
    GET    /service-catalog/{entity_id}  read one as JSON-LD
    POST   /service-catalog              create (validates + stores)
    PUT    /service-catalog/{entity_id}  create or replace (idempotent)
    DELETE /service-catalog/{entity_id}  delete and its subtree

Isolated in its own named graph (entity URI = named graph), in the same
repository as Category, Candidate, and ConcreteCFSS, so the typical
discovery query stays a plain SPARQL query with no federation.
"""

from __future__ import annotations

from app.config import EntityType
from app.routers.entities_explicit._factory import ExplicitEntityRoute, build_entity_router

ROUTE_SPEC = ExplicitEntityRoute(
    segment="service-catalog",
    entity_type=EntityType.SERVICE_CATALOG,
    rdf_class="ServiceCatalog",
    display_name="Service Catalog",
    schema_file="service_catalog.schema.json",
)
router = build_entity_router(ROUTE_SPEC)
