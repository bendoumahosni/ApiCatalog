"""Explicit CRUD routes for RFSS (Resource-Facing Service Specification).

    GET    /concret-rfs              list all RFSS instances
    GET    /concret-rfs/{entity_id}  read one as JSON-LD
    POST   /concret-rfs              create (validates + stores)
    PUT    /concret-rfs/{entity_id}  create or replace (idempotent)
    DELETE /concret-rfs/{entity_id}  delete and its subtree

Isolated in its own named graph (entity URI = named graph). RFSS are the
leaves of the dependency chain (no outgoing strong reference of their own)
and must be ingested first, since GenericCFSS/ConcreteCFSS depend on them.

Its URI is minted from `id` alone (no type prefix): the bundled example uses
a placeholder id, so replace it with a real, unique value before posting.
"""

from __future__ import annotations

from app.config import EntityType
from app.routers.entities_explicit._factory import ExplicitEntityRoute, build_entity_router

ROUTE_SPEC = ExplicitEntityRoute(
    segment="concret-rfs",
    entity_type=EntityType.CONCRETE_RFSS,
    rdf_class="RFS",
    display_name="RFSS",
    schema_file="rfss.schema.json",
    id_only_uri=True,
)
router = build_entity_router(ROUTE_SPEC)
