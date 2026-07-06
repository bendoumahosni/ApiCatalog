"""Explicit, entity-specific CRUD routers -- one file per TMF entity type.

Each module here (service_catalog, service_category, service_candidate,
generic_cfs, concret_cfs, concret_rfs, service_profile) declares exactly one
APIRouter bound to one EntityType, built by the shared factory in
_factory.py. This package's ALL_ROUTERS list is what app/main.py mounts.

These cover every TMF entity type's full CRUD. ``concret_cfs`` additionally
registers the catalog discovery endpoints (``GET /concret-cfs/filter``,
``POST /concret-cfs/search``) on its own router, before its generic
``{entity_id}`` route -- see ``_factory.py``'s module docstring for why that
ordering is required.
"""

from __future__ import annotations

from app.routers.entities_explicit import (
    concret_cfs,
    concret_rfs,
    generic_cfs,
    service_candidate,
    service_catalog,
    service_category,
    service_profile,
)
from app.routers.entities_explicit._factory import ExplicitEntityRoute

ALL_ROUTERS = [
    service_catalog.router,
    service_category.router,
    service_candidate.router,
    generic_cfs.router,
    concret_cfs.router,
    concret_rfs.router,
    service_profile.router,
]

# The ExplicitEntityRoute spec behind each router above, in the same order.
# Exposed for tooling/tests that need the dataclass-level info (entity_type,
# schema_file, id_only_uri, ...), not just the assembled APIRouter.
ROUTE_SPECS: list[ExplicitEntityRoute] = [
    service_catalog.ROUTE_SPEC,
    service_category.ROUTE_SPEC,
    service_candidate.ROUTE_SPEC,
    generic_cfs.ROUTE_SPEC,
    concret_cfs.ROUTE_SPEC,
    concret_rfs.ROUTE_SPEC,
    service_profile.ROUTE_SPEC,
]
