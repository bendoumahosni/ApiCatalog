"""Explicit CRUD routes for ServiceManagementProfile.

    GET    /service-profile              list all ServiceManagementProfile instances
    GET    /service-profile/{entity_id}  read one as JSON-LD
    POST   /service-profile              create (validates + stores)
    PUT    /service-profile/{entity_id}  create or replace (idempotent)
    DELETE /service-profile/{entity_id}  delete and its subtree

Isolated in its own named graph (entity URI = named graph). Ingestion
currently maps the generic ServiceSpecification fields only;
management-interface/lifecycle-operation-specific fields are not yet mapped
by the converter (see project README, "known follow-ups").

Its URI is minted from `id` alone (no type prefix): the bundled example uses
a placeholder id, so replace it with a real, unique value before posting.
"""

from __future__ import annotations

from app.config import EntityType
from app.routers.entities_explicit._factory import ExplicitEntityRoute, build_entity_router

ROUTE_SPEC = ExplicitEntityRoute(
    segment="service-profile",
    entity_type=EntityType.PROFILE_MANAGEMENT,
    rdf_class="ServiceManagementProfile",
    display_name="Service Management Profile",
    schema_file="service_profile.schema.json",
    id_only_uri=True,
)
router = build_entity_router(ROUTE_SPEC)
