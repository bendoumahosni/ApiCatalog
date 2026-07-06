"""Explicit CRUD routes for GenericCFSS.

    GET    /generic-cfs              list all GenericCFSS instances
    GET    /generic-cfs/{entity_id}  read one as JSON-LD
    POST   /generic-cfs              create (validates + stores)
    PUT    /generic-cfs/{entity_id}  create or replace (idempotent)
    DELETE /generic-cfs/{entity_id}  delete and its subtree

Isolated in its own named graph (entity URI = named graph). Per TMF633, a
GenericCFSS declares the admissible range or set of options for a
characteristic, not a single committed value -- those live on the
ConcreteCFSS that specializes it.

Its URI is minted from `id` alone (no type prefix): the bundled example uses
a placeholder id, so replace it with a real, unique value before posting.
"""

from __future__ import annotations

from app.config import EntityType
from app.routers.entities_explicit._factory import ExplicitEntityRoute, build_entity_router

ROUTE_SPEC = ExplicitEntityRoute(
    segment="generic-cfs",
    entity_type=EntityType.GENERIC_CFSS,
    rdf_class="GenericCFSS",
    display_name="Generic CFSS",
    schema_file="generic_cfss.schema.json",
    id_only_uri=True,
)
router = build_entity_router(ROUTE_SPEC)
