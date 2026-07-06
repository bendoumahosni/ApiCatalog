"""Explicit CRUD routes for ConcreteCFSS.

    GET    /concret-cfs              list all ConcreteCFSS instances
    GET    /concret-cfs/{entity_id}  read one as JSON-LD
    POST   /concret-cfs              create (validates + stores)
    PUT    /concret-cfs/{entity_id}  create or replace (idempotent)
    DELETE /concret-cfs/{entity_id}  delete and its subtree
    GET    /concret-cfs/filter       catalog discovery (local, see below)
    POST   /concret-cfs/search       multi-criteria search (local + generic)

Isolated in its own named graph (entity URI = named graph), inside the same
repository as ServiceCatalog, ServiceCategory, and ServiceCandidate, so the
typical discovery query (catalog -> category -> candidate -> concrete spec)
is a plain SPARQL query with no federation.
A ConcreteCFSS must specialize a GenericCFSS (enforced by SHACL).

``filter``/``search`` are registered on *this* router, before the generic
``GET /concret-cfs/{entity_id}`` route, via ``register_before`` — see
``_factory.py``'s module docstring for why that ordering is required (a
literal sibling path declared on a different, later-included router is
permanently shadowed by this router's ``{entity_id}`` catch-all, since
Starlette resolves against the flattened route table in registration order
across all included routers, not router-by-router).

Its URI is minted from `id` alone (no type prefix): the bundled example uses
a placeholder id, so replace it with a real, unique value before posting.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from app.config import EntityType, Settings
from app.deps import settings_dep
from app.graphdb.client import get_client
from app.models.schemas import QueryResult
from app.routers.entities_explicit._factory import ExplicitEntityRoute, build_entity_router
from app.routers.entities import SearchRequest
from app.services.query import Criterion, build_combined_query, build_concrete_cfss_query


def _register_discovery_routes(router: APIRouter) -> None:
    @router.get(
        "/concret-cfs/filter",
        response_model=QueryResult,
        summary="Find concrete CFSS, optionally filtered by catalog/category/characteristic",
        operation_id="filter_concret_cfs",
    )
    async def search_concrete_cfss(
        catalog: Optional[str] = Query(None, description="Filter by catalog name"),
        category: Optional[str] = Query(None, description="Filter by category name"),
        characteristic: Optional[str] = Query(None, description="Characteristic name, e.g. 'Availability'"),
        value: Optional[str] = Query(None, description="Required characteristic value, e.g. 'High'"),
        limit: int = Query(100, ge=1, le=1000),
        settings: Settings = Depends(settings_dep),
    ) -> QueryResult:
        query = build_concrete_cfss_query(
            catalog_name=catalog, category_name=category,
            characteristic=characteristic, char_value=value, limit=limit,
        )
        rows = await get_client().select(settings.repo_concret_cfs, query)
        return QueryResult(count=len(rows), federated=False, results=rows)

    @router.post(
        "/concret-cfs/search",
        response_model=QueryResult,
        summary="Multi-criteria search: combine several characteristics",
        operation_id="search_concret_cfs",
    )
    async def search_concrete_combined(
        payload: SearchRequest = Body(...),
        settings: Settings = Depends(settings_dep),
    ) -> QueryResult:
        """Find ConcreteCFSS matching ALL given criteria at once.

        Each criterion is a (characteristic, value) pair tagged with where
        that characteristic lives:
          * "local"   -> defined directly on the ConcreteCFSS
          * "generic" -> defined on its GenericCFSS parent (same repository,
            matched via an ordinary join across named graphs — no SERVICE
            federation needed under the single-repository layout)
        """
        criteria = [
            Criterion(characteristic=c.characteristic, value=c.value, scope=c.scope)
            for c in payload.criteria
        ]
        query = build_combined_query(
            settings, criteria=criteria,
            catalog_name=payload.catalog, category_name=payload.category,
            limit=payload.limit,
        )
        federated = any(c.scope == "generic" for c in payload.criteria)
        rows = await get_client().select(settings.repo_concret_cfs, query)
        return QueryResult(count=len(rows), federated=federated, results=rows)


ROUTE_SPEC = ExplicitEntityRoute(
    segment="concret-cfs",
    entity_type=EntityType.CONCRETE_CFSS,
    rdf_class="ConcreteCFSS",
    display_name="Concrete CFSS",
    schema_file="concret_cfss.schema.json",
    id_only_uri=True,
    register_before=_register_discovery_routes,
)
router = build_entity_router(ROUTE_SPEC)
