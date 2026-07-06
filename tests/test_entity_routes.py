"""Tests for app/routers/entities_explicit/ -- explicit per-entity CRUD routes.

Covers:
* every entity's bundled Swagger example actually passes the JSON contract
  and SHACL layers (so a user who copy-pastes the example gets a real
  success, not a confusing validation error on the very first try);
* POST/PUT to an entity-specific route rejects a document detected as a
  different TMF type (EntityTypeMismatchError), proving the routing is
  enforced explicitly rather than silently re-routing dynamically;
* the full route table matches the requested entity list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from rdflib import Graph

from app.config import EntityType, get_settings
from app.converter.json_to_ttl import JsonToGraphConverter
from app.core.exceptions import EntityTypeMismatchError
from app.routers.entities_explicit import ROUTE_SPECS as ROUTES
from app.routers.entities_explicit._factory import load_example as _load_example
from app.services.ingestion import IngestionService
from app.validation.json_contract import JsonContractValidator
from app.validation.shacl_validator import ShaclValidator

BASE = Path(__file__).resolve().parent.parent
ONT = BASE / "ontology" / "tmf-service-core.ttl"
SHP = BASE / "shapes" / "shapes.ttl"


@pytest.fixture(scope="module")
def converter() -> JsonToGraphConverter:
    return JsonToGraphConverter(ONT)


@pytest.fixture(scope="module")
def shacl() -> ShaclValidator:
    return ShaclValidator(SHP, ONT)


@pytest.fixture(scope="module")
def json_contract() -> JsonContractValidator:
    return JsonContractValidator()


@pytest.mark.parametrize("route_spec", ROUTES, ids=lambda r: r.segment)
def test_bundled_example_detected_as_expected_type(converter, route_spec):
    example = _load_example(route_spec.schema_file)
    detected = converter.detect_entity_type(example)
    assert detected == route_spec.entity_type


@pytest.mark.parametrize("route_spec", ROUTES, ids=lambda r: r.segment)
def test_bundled_example_passes_json_contract(converter, json_contract, route_spec):
    example = _load_example(route_spec.schema_file)
    json_contract.validate(example, route_spec.entity_type)


@pytest.mark.parametrize("route_spec", ROUTES, ids=lambda r: r.segment)
def test_bundled_example_passes_shacl(converter, shacl, route_spec):
    example = _load_example(route_spec.schema_file)
    graph = converter.convert(example, route_spec.entity_type)
    uri = str(converter.entity_uri(example, route_spec.entity_type))
    shacl.validate(graph, route_spec.entity_type, uri)


class _FakeGraphDB:
    def __init__(self) -> None:
        self.graphs: dict[tuple[str, str], Graph] = {}

    async def ask(self, repository: str, query: str) -> bool:
        m = re.search(r"<([^>]+)>", query)
        if not m:
            return False
        uri = m.group(1)
        for (repo, _ng), g in self.graphs.items():
            if repo == repository and any(str(s) == uri for s, _p, _o in g):
                return True
        return False

    async def graph_exists(self, repository: str, named_graph: str) -> bool:
        return (repository, named_graph) in self.graphs

    async def add_graph(self, repository, graph, named_graph) -> None:
        self.graphs[(repository, named_graph)] = graph

    async def replace_graph(self, repository, graph, named_graph) -> None:
        self.graphs[(repository, named_graph)] = graph

    async def drop_graph(self, repository, named_graph) -> None:
        self.graphs.pop((repository, named_graph), None)


def _service(fake, converter, shacl, json_contract) -> IngestionService:
    return IngestionService(get_settings(), fake, converter, shacl, json_contract)


def _load_example_for(entity_type: EntityType) -> dict:
    spec = next(r for r in ROUTES if r.entity_type == entity_type)
    return _load_example(spec.schema_file)


@pytest.mark.asyncio
async def test_explicit_route_accepts_matching_type(converter, shacl, json_contract):
    fake = _FakeGraphDB()
    svc = _service(fake, converter, shacl, json_contract)
    catalog = _load_example_for(EntityType.SERVICE_CATALOG)
    res = await svc.ingest(catalog, expected_entity_type=EntityType.SERVICE_CATALOG)
    assert res.status == "created"
    assert res.entity_type == EntityType.SERVICE_CATALOG


@pytest.mark.asyncio
async def test_explicit_route_rejects_mismatched_type(converter, shacl, json_contract):
    fake = _FakeGraphDB()
    svc = _service(fake, converter, shacl, json_contract)
    catalog = _load_example_for(EntityType.SERVICE_CATALOG)
    with pytest.raises(EntityTypeMismatchError) as excinfo:
        await svc.ingest(catalog, expected_entity_type=EntityType.SERVICE_CANDIDATE)
    assert "service_candidate" in excinfo.value.message
    assert "service_catalog" in excinfo.value.message


@pytest.mark.asyncio
async def test_explicit_route_mismatch_also_enforced_on_dry_run(converter, shacl, json_contract):
    fake = _FakeGraphDB()
    svc = _service(fake, converter, shacl, json_contract)
    catalog = _load_example_for(EntityType.SERVICE_CATALOG)
    with pytest.raises(EntityTypeMismatchError):
        await svc.dry_run(catalog, expected_entity_type=EntityType.CONCRETE_RFSS)


def test_seven_entities_registered_with_full_crud():
    from fastapi.testclient import TestClient
    from app.deps import init_singletons
    from app.graphdb.client import init_client

    init_singletons()
    init_client(get_settings())
    from app.main import app

    client = TestClient(app)
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    expected_segments = {
        "service-catalog", "service-category", "service-candidate",
        "generic-cfs", "concret-cfs", "concret-rfs", "service-profile",
    }
    for segment in expected_segments:
        assert f"/{segment}" in paths
        assert f"/{segment}/{{entity_id}}" in paths
        methods_collection = set(paths[f"/{segment}"].keys())
        methods_item = set(paths[f"/{segment}/{{entity_id}}"].keys())
        assert {"get", "post"} <= methods_collection
        assert {"get", "put", "delete"} <= methods_item


def test_each_entity_gets_its_own_swagger_section_not_a_shared_one():
    """Swagger groups operations by tag into doc sections. Each of the 7
    entities must get its own tag (its display name, e.g. "Service Catalog")
    so they render as 7 separate sections -- not all lumped together under a
    single generic "entities (explicit)" tag, which was the earlier (wrong)
    behaviour."""
    from fastapi.testclient import TestClient
    from app.deps import init_singletons
    from app.graphdb.client import init_client

    init_singletons()
    init_client(get_settings())
    from app.main import app

    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    expected_tags = {
        "Service Catalog", "Service Category", "Service Candidate",
        "Generic CFSS", "Concrete CFSS", "RFSS", "Service Management Profile",
    }
    found_tags: set[str] = set()
    for methods in schema["paths"].values():
        for op in methods.values():
            found_tags.update(op.get("tags", []))

    assert expected_tags <= found_tags
    assert "entities (explicit)" not in found_tags

    # Each entity's 5 operations must carry exactly its own tag, not be mixed
    # with another entity's.
    segment_to_tag = {
        "service-catalog": "Service Catalog",
        "service-category": "Service Category",
        "service-candidate": "Service Candidate",
        "generic-cfs": "Generic CFSS",
        "concret-cfs": "Concrete CFSS",
        "concret-rfs": "RFSS",
        "service-profile": "Service Management Profile",
    }
    for segment, expected_tag in segment_to_tag.items():
        for path_suffix in ("", "/{entity_id}"):
            path = f"/{segment}{path_suffix}"
            for op in schema["paths"][path].values():
                assert op["tags"] == [expected_tag]
