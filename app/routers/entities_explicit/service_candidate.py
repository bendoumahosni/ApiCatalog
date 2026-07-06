"""Explicit CRUD routes for ServiceCandidate.

    GET    /service-candidate              list all ServiceCandidate instances
    GET    /service-candidate/{entity_id}  read one as JSON-LD
    POST   /service-candidate              create (validates + stores)
    PUT    /service-candidate/{entity_id}  create or replace (idempotent)
    DELETE /service-candidate/{entity_id}  delete and its subtree

A candidate exposes exactly one ConcreteCFSS (enforced by SHACL) and is
isolated in its own named graph, in the same repository as the concrete
spec it exposes.
"""

from __future__ import annotations

from app.config import EntityType
from app.routers.entities_explicit._factory import ExplicitEntityRoute, build_entity_router

ROUTE_SPEC = ExplicitEntityRoute(
    segment="service-candidate",
    entity_type=EntityType.SERVICE_CANDIDATE,
    rdf_class="ServiceCandidate",
    display_name="Service Candidate",
    schema_file="service_candidate.schema.json",
)
router = build_entity_router(ROUTE_SPEC)
