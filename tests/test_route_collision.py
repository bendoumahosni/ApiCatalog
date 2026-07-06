"""Route registration tests.

Historical note: an earlier version of this API had a literal-path collision
bug (GET /concret-cfs was shadowed by a filtered-search handler that never
projected ?id). That bug was fixed by moving the filtered search to
/concret-cfs/filter, and *separately*, the API later gained explicit
per-entity routes (this module's subject): /concret-cfs is now intentionally
a real, first-class route again -- registered explicitly in
app/routers/entities_explicit/, not the dynamic /{kind} catch-all -- and must
behave like the other 6 entity routes, not collide with anything.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import init_singletons
from app.graphdb.client import init_client


def _client() -> TestClient:
    init_singletons()
    init_client(get_settings())
    from app.main import app
    return TestClient(app)


def test_concret_cfs_is_an_explicit_route_not_the_generic_catch_all():
    """GET /concret-cfs must resolve to the explicit per-entity router
    (operationId 'list_concret_cfs'), not the generic /{kind} one."""
    c = _client()
    schema = c.get("/openapi.json").json()
    op_id = schema["paths"]["/concret-cfs"]["get"]["operationId"]
    assert op_id == "list_concret_cfs"


def test_filter_and_search_subpaths_still_exist_and_dont_collide():
    """/concret-cfs/filter (typical query) and /concret-cfs/search (combined
    multi-criteria, POST only after the GET federated convenience endpoint
    was removed) must both still resolve independently of /concret-cfs."""
    c = _client()
    schema = c.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/concret-cfs/filter" in paths
    assert "get" in paths["/concret-cfs/filter"]

    assert "/concret-cfs/search" in paths
    assert "post" in paths["/concret-cfs/search"]
    assert "get" not in paths["/concret-cfs/search"]  # removed per request


def test_filtered_search_query_projects_id():
    """Whichever path the filtered/typical search lives at, its SPARQL query
    must project ?id -- a row without an id cannot be resolved to a detail
    view or a delete operation by any client."""
    from app.services.query import build_concrete_cfss_query

    query = build_concrete_cfss_query(catalog_name="NF_MUST_Catalog")
    assert "?id" in query
    assert "tmf:id ?id" in query


def test_all_seven_entities_have_explicit_crud_routes():
    """Every one of the 7 TMF entity types must expose GET (list), GET
    (detail), POST, PUT, DELETE at its own literal path."""
    c = _client()
    schema = c.get("/openapi.json").json()
    paths = schema["paths"]

    segments = [
        "service-catalog", "service-category", "service-candidate",
        "generic-cfs", "concret-cfs", "concret-rfs", "service-profile",
    ]
    for segment in segments:
        assert segment in [s.strip("/") for s in [segment]]  # sanity no-op
        collection = f"/{segment}"
        item = f"/{segment}/{{entity_id}}"
        assert collection in paths, f"missing collection path for {segment}"
        assert item in paths, f"missing item path for {segment}"
        assert "get" in paths[collection]
        assert "post" in paths[collection]
        assert "get" in paths[item]
        assert "put" in paths[item]
        assert "delete" in paths[item]


def test_removed_endpoints_are_gone():
    """GET / (root) and GET /concret-cfs/search were explicitly removed.

    Note: /health (and /ready) were previously removed but have been
    deliberately reintroduced for production deployment — /health backs the
    container HEALTHCHECK (liveness, no GraphDB dependency) and /ready backs a
    readiness probe (checks GraphDB). See test_health_and_ready_present below.
    """
    c = _client()
    schema = c.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/" not in paths
    assert "get" not in paths.get("/concret-cfs/search", {})


def test_health_and_ready_present():
    """The deployment probes exist: /health (liveness) and /ready (readiness)."""
    c = _client()
    paths = c.get("/openapi.json").json()["paths"]
    assert "get" in paths.get("/health", {})
    assert "get" in paths.get("/ready", {})
    # /health must be answerable without GraphDB (liveness).
    assert c.get("/health").status_code == 200
