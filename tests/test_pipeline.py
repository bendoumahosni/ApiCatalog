"""Test suite.

* Converter and SHACL tests run fully offline.
* Pipeline tests use an in-memory fake GraphDB client, so the orchestration
  (type routing, SHACL gate, reference validation, named-graph write,
  create-vs-replace) is exercised without a live GraphDB.

Run with:  pytest -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import Graph

from app.config import EntityType, get_settings
from app.converter.json_to_ttl import JsonToGraphConverter
from app.core.exceptions import (
    EntityAlreadyExistsError,
    MissingReferenceError,
    ValidationError,
)
from app.services.ingestion import IngestionService
from app.validation.json_contract import JsonContractValidator
from app.validation.shacl_validator import ShaclValidator

BASE = Path(__file__).resolve().parent.parent
ONT = BASE / "ontology" / "tmf-service-core.ttl"
SHP = BASE / "shapes" / "shapes.ttl"
EX = BASE / "examples"


def _load(name: str) -> dict:
    return json.loads((EX / name).read_text())


@pytest.fixture(scope="module")
def converter() -> JsonToGraphConverter:
    return JsonToGraphConverter(ONT)


@pytest.fixture(scope="module")
def shacl() -> ShaclValidator:
    return ShaclValidator(SHP, ONT)


@pytest.fixture(scope="module")
def json_contract() -> JsonContractValidator:
    return JsonContractValidator()


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fname,expected", [
    ("service_catalog.json", EntityType.SERVICE_CATALOG),
    ("service_category.json", EntityType.SERVICE_CATEGORY),
    ("service_candidate.json", EntityType.SERVICE_CANDIDATE),
    ("Concret_CFSS.json", EntityType.CONCRETE_CFSS),
    ("eMBB_Generic_CFSS.json", EntityType.GENERIC_CFSS),
])
def test_type_detection(converter, fname, expected):
    assert converter.detect_entity_type(_load(fname)) == expected


def test_cross_repo_uris_match(converter):
    """The candidate's exposed spec and the concrete self-URI must coincide,
    and the concrete's specializes target must equal the generic self-URI.

    With the per-type graph URIs, spec_uri is type-aware: the exposed spec is
    a ConcreteCFSS, the specializes target is a GenericCFSS. The point of this
    test is that both sides of each cross-entity link mint the SAME URI."""
    cand = _load("service_candidate.json")
    con = _load("Concret_CFSS.json")
    gen = _load("eMBB_Generic_CFSS.json")

    exposed = str(converter.spec_uri(cand["serviceSpecification"]["id"], "ConcreteCFSS"))
    concrete_self = str(converter.entity_uri(con, EntityType.CONCRETE_CFSS))
    assert exposed == concrete_self

    specializes_target = next(
        str(converter.spec_uri(r["serviceSpec"]["id"], "GenericCFSS"))
        for r in con["serviceSpecRelationship"]
        if r["relationshipType"] == "specializes"
    )
    generic_self = str(converter.entity_uri(gen, EntityType.GENERIC_CFSS))
    assert specializes_target == generic_self


def test_exposed_spec_typed_concrete(converter):
    cand = _load("service_candidate.json")
    g = converter.convert(cand, EntityType.SERVICE_CANDIDATE)
    types = {str(o).split("#")[-1] for s, p, o in g
             if str(p).endswith("type") and "cfss-embb" in str(s)}
    assert "ConcreteCFSS" in types
    assert "GenericCFSS" not in types


# --------------------------------------------------------------------------- #
# SHACL
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fname", [
    "service_catalog.json", "service_category.json", "service_candidate.json",
    "Concret_CFSS.json", "eMBB_Generic_CFSS.json",
])
def test_valid_examples_conform(converter, shacl, fname):
    data = _load(fname)
    et = converter.detect_entity_type(data)
    g = converter.convert(data, et)
    shacl.validate(g, et, str(converter.entity_uri(data, et)))


def test_candidate_without_exposes_rejected(converter, shacl):
    data = _load("service_candidate.json")
    data.pop("serviceSpecification")
    et = converter.detect_entity_type(data)
    g = converter.convert(data, et)
    with pytest.raises(ValidationError):
        shacl.validate(g, et, str(converter.entity_uri(data, et)))


# --------------------------------------------------------------------------- #
# Pipeline with a fake GraphDB
# --------------------------------------------------------------------------- #
class FakeGraphDB:
    """Minimal in-memory stand-in for GraphDBClient."""

    def __init__(self, existing_uris: set[str] | None = None):
        self.graphs: dict[tuple[str, str], Graph] = {}
        self.existing = existing_uris or set()

    async def ask(self, repository: str, query: str) -> bool:
        # Existence checks used by the reference validator: <uri> ?p ?o
        for uri in self.existing:
            if f"<{uri}>" in query:
                return True
        return False

    async def graph_exists(self, repository: str, named_graph: str) -> bool:
        return (repository, named_graph) in self.graphs

    async def add_graph(self, repository, graph, named_graph):
        self.graphs[(repository, named_graph)] = graph

    async def replace_graph(self, repository, graph, named_graph):
        self.graphs[(repository, named_graph)] = graph

    async def drop_graph(self, repository, named_graph):
        self.graphs.pop((repository, named_graph), None)


def _service(fake, converter, shacl, json_contract) -> IngestionService:
    return IngestionService(get_settings(), fake, converter, shacl, json_contract)


def _service_with_ref_checks(fake, converter, shacl, json_contract) -> IngestionService:
    """Service with strong-reference checks ON (the order-enforcing behaviour,
    disabled by default in Settings). Used by the tests that specifically
    assert the ordering is enforced when the flag is enabled."""
    settings = get_settings().model_copy(update={"reference_checks_enabled": True})
    return IngestionService(settings, fake, converter, shacl, json_contract)


@pytest.mark.asyncio
async def test_ingest_generic_routes_and_writes(converter, shacl, json_contract):
    # The generic CFSS dependsOn two RFSS, which must exist first. Their
    # existence URIs are minted under the /concret_rfs/ segment, same as the
    # converter uses for a dependsOn target.
    existing = {
        str(converter.spec_uri("cn-nsst-embb-001", "RFSS")),
        str(converter.spec_uri("ran-nsst-embb-001", "RFSS")),
    }
    fake = FakeGraphDB(existing_uris=existing)
    svc = _service(fake, converter, shacl, json_contract)
    res = await svc.ingest(_load("eMBB_Generic_CFSS.json"))
    assert res.status == "created"
    assert res.repository == get_settings().repo_generic_cfs
    assert res.entity_uri.endswith("/7659")
    assert (get_settings().repo_generic_cfs, res.entity_uri) in fake.graphs


@pytest.mark.asyncio
async def test_generic_blocked_without_its_rfss(converter, shacl, json_contract):
    # Only blocks when reference checks are explicitly enabled.
    fake = FakeGraphDB(existing_uris=set())
    svc = _service_with_ref_checks(fake, converter, shacl, json_contract)
    with pytest.raises(MissingReferenceError):
        await svc.ingest(_load("eMBB_Generic_CFSS.json"))


@pytest.mark.asyncio
async def test_concrete_missing_reference_blocks(converter, shacl, json_contract):
    fake = FakeGraphDB(existing_uris=set())  # nothing exists yet
    svc = _service_with_ref_checks(fake, converter, shacl, json_contract)
    with pytest.raises(MissingReferenceError):
        await svc.ingest(_load("Concret_CFSS.json"))


@pytest.mark.asyncio
async def test_reference_checks_disabled_by_default(converter, shacl, json_contract):
    """With the default settings (checks OFF), a document whose references do
    not exist yet ingests anyway — order is not enforced."""
    fake = FakeGraphDB(existing_uris=set())
    svc = _service(fake, converter, shacl, json_contract)
    res = await svc.ingest(_load("Concret_CFSS.json"))
    assert res.status == "created"


@pytest.mark.asyncio
async def test_concrete_ingests_when_references_exist(converter, shacl, json_contract):
    # The concrete CFSS specializes generic 7659 and dependsOn two RFSS; each
    # existence URI is minted under its own per-type segment.
    existing = {
        str(converter.spec_uri("7659", "GenericCFSS")),
        str(converter.spec_uri("cn-nsst-embb-001", "RFSS")),
        str(converter.spec_uri("ran-nsst-embb-001", "RFSS")),
    }
    fake = FakeGraphDB(existing_uris=existing)
    svc = _service_with_ref_checks(fake, converter, shacl, json_contract)
    res = await svc.ingest(_load("Concret_CFSS.json"))
    assert res.status == "created"
    assert res.repository == get_settings().repo_concret_cfs


@pytest.mark.asyncio
async def test_duplicate_create_rejected_then_put_replaces(converter, shacl, json_contract):
    fake = FakeGraphDB()
    svc = _service(fake, converter, shacl, json_contract)
    await svc.ingest(_load("service_catalog.json"))
    with pytest.raises(EntityAlreadyExistsError):
        await svc.ingest(_load("service_catalog.json"))
    res = await svc.ingest(_load("service_catalog.json"), replace=True)
    assert res.status == "replaced"


# --------------------------------------------------------------------------- #
# JSON contract (input-side) validation
# --------------------------------------------------------------------------- #
def test_contract_accepts_valid_examples(converter, json_contract):
    for fname in ("service_catalog.json", "service_category.json",
                  "service_candidate.json", "Concret_CFSS.json", "eMBB_Generic_CFSS.json"):
        data = _load(fname)
        json_contract.validate(data, converter.detect_entity_type(data))


def test_contract_tolerates_unknown_uuid_field(converter, json_contract):
    """OpenSlice specs carry a `uuid` field the contract does not declare; it
    must be tolerated, not rejected."""
    data = _load("eMBB_Generic_CFSS.json")
    data["uuid"] = "b3ad0cff-cf8b-8a85-1234-567890abcdef"
    json_contract.validate(data, EntityType.GENERIC_CFSS)  # must not raise


def test_contract_tolerates_null_optional_fields(converter, json_contract):
    """OpenSlice emits null for optional fields it has no value for
    (href, @schemaLocation, version, ...). Null on an OPTIONAL field is
    accepted; an absent field would have been too."""
    data = _load("eMBB_Generic_CFSS.json")
    data["href"] = None
    data["@schemaLocation"] = None
    data["version"] = None
    json_contract.validate(data, EntityType.GENERIC_CFSS)  # must not raise


def test_contract_tolerates_null_in_nested_reference(converter, json_contract):
    """Null on an optional field of a nested object (e.g. a category ref's
    href/name) is tolerated too — the nullability is applied recursively."""
    data = _load("service_candidate.json")
    data["category"][0]["href"] = None
    json_contract.validate(data, EntityType.SERVICE_CANDIDATE)  # must not raise


def test_contract_still_rejects_null_required_id(converter, json_contract):
    """A null on a REQUIRED field is still a real error and must be rejected."""
    data = _load("eMBB_Generic_CFSS.json")
    data["id"] = None
    with pytest.raises(ValidationError):
        json_contract.validate(data, EntityType.GENERIC_CFSS)


def test_contract_opentslice_like_spec_passes(converter, json_contract):
    """End-to-end of the reported failure: uuid + null href + null
    @schemaLocation together."""
    doc = {
        "@type": "CustomerFacingServiceSpecification",
        "id": "7659",
        "name": "eMBB_CFSS",
        "uuid": "b3ad0cff-cf8b-8a85",
        "href": None,
        "@schemaLocation": None,
        "version": None,
        "serviceSpecCharacteristic": [],
    }
    json_contract.validate(doc, EntityType.GENERIC_CFSS)  # must not raise


def test_contract_rejects_organizedInto_instead_of_category(converter, json_contract):
    data = _load("service_catalog.json")
    data["organizedInto"] = data.pop("category")
    with pytest.raises(ValidationError):
        json_contract.validate(data, converter.detect_entity_type(data))


def test_contract_rejects_exposes_instead_of_serviceSpecification(converter, json_contract):
    data = _load("service_candidate.json")
    data["exposes"] = data.pop("serviceSpecification")
    with pytest.raises(ValidationError):
        json_contract.validate(data, converter.detect_entity_type(data))


def test_contract_rejects_serviceSpecification_missing_id(converter, json_contract):
    data = _load("service_candidate.json")
    data["serviceSpecification"].pop("id")
    with pytest.raises(ValidationError):
        json_contract.validate(data, converter.detect_entity_type(data))


def test_contract_rejects_serviceSpecification_missing_name(converter, json_contract):
    data = _load("service_candidate.json")
    data["serviceSpecification"].pop("name")
    with pytest.raises(ValidationError):
        json_contract.validate(data, converter.detect_entity_type(data))


@pytest.mark.asyncio
async def test_ingest_fails_fast_on_bad_contract_before_conversion(converter, shacl, json_contract):
    """A JSON contract violation must be caught before any GraphDB write."""
    fake = FakeGraphDB()
    svc = _service(fake, converter, shacl, json_contract)
    data = _load("service_candidate.json")
    data["exposes"] = data.pop("serviceSpecification")
    with pytest.raises(ValidationError):
        await svc.ingest(data)
    assert fake.graphs == {}


def test_missing_reference_report_uses_json_field_name(converter):
    """The missing-reference report must speak JSON ('serviceSpecification'),
    never the TTL predicate name ('exposes'), since this report describes a
    problem in the user's *input JSON*."""
    from app.validation.references import ReferenceValidator

    class _NothingExistsClient:
        async def ask(self, repository, query):
            return False

    rv = ReferenceValidator(get_settings(), _NothingExistsClient(), converter)
    refs = rv.collect(_load("service_candidate.json"), EntityType.SERVICE_CANDIDATE)
    assert len(refs) == 1
    assert refs[0].json_field == "serviceSpecification"


@pytest.mark.parametrize("field,value", [
    ("rangeInterval", None), ("unitOfMeasure", None),
    ("valueFrom", None), ("valueTo", None), ("regex", None),
])
def test_contract_allows_null_descriptive_characteristic_value_fields(converter, json_contract, field, value):
    """Descriptive/optional TMF633 fields on a characteristic value (not
    structural references) must never block ingestion when null."""
    data = _load("Concret_CFSS.json")
    data["serviceSpecCharacteristic"][0]["serviceSpecCharacteristicValue"][0][field] = value
    json_contract.validate(data, EntityType.CONCRETE_CFSS)  # must not raise


@pytest.mark.parametrize("field", ["extensible", "regex", "maxCardinality", "minCardinality", "valueType"])
def test_contract_allows_null_descriptive_characteristic_fields(converter, json_contract, field):
    data = _load("Concret_CFSS.json")
    data["serviceSpecCharacteristic"][0][field] = None
    json_contract.validate(data, EntityType.CONCRETE_CFSS)  # must not raise


# --------------------------------------------------------------------------- #
# GET /{kind} -- list_entities query construction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rdf_class", [
    "ConcreteCFSS", "RFS", "GenericCFSS", "ServiceManagementProfile",
    "ServiceCatalog", "Category", "ServiceCandidate",
])
def test_list_entities_query_is_valid_sparql_for_every_kind(rdf_class):
    """Regression test: list_entities used to mix an un-prefixed string with
    f-strings, doubling the OPTIONAL braces ('{{ ... }}' emitted literally)
    and producing a 400 from GraphDB for every /{kind} except by accident."""
    from rdflib.plugins.sparql import prepareQuery

    # Build with the exact same logic as IngestionService.list_entities,
    # without needing a live GraphDB client.
    limit = 100
    query = (
        "PREFIX tmf: <http://vocab.nfmust.ie/tmf-service-core#>\n"
        f"SELECT ?uri ?id ?name WHERE {{\n"
        f"  ?uri a tmf:{rdf_class} .\n"
        f"  OPTIONAL {{ ?uri tmf:id ?id }}\n"
        f"  OPTIONAL {{ ?uri tmf:name ?name }}\n"
        f"}}\n"
        f"LIMIT {int(limit)}"
    )
    assert "{{" not in query and "}}" not in query
    prepareQuery(query)  # raises if not valid SPARQL


@pytest.mark.asyncio
async def test_list_entities_calls_select_with_well_formed_query(converter, shacl, json_contract):
    captured = {}

    class _RecordingClient:
        async def select(self, repository, query):
            captured["repository"] = repository
            captured["query"] = query
            return []

    svc = IngestionService(get_settings(), _RecordingClient(), converter, shacl, json_contract)
    rows = await svc.list_entities(get_settings().repository, "RFS", limit=50)
    assert rows == []
    assert "{{" not in captured["query"]
    assert "}}" not in captured["query"]
    assert "LIMIT 50" in captured["query"]

# --------------------------------------------------------------------------- #
# ConcreteCFSS without `specializes` (bulk ingestion from external catalogues)
# --------------------------------------------------------------------------- #
def test_concrete_without_specializes_is_typed_concrete(converter):
    """A ConcreteCFSS with no serviceSpecRelationship at all must still be
    recognised and typed ConcreteCFSS when the route declares the type."""
    doc = {
        "@type": "CustomerFacingServiceSpecification",
        "id": "concrete-no-spec-001",
        "name": "Offer with no specializes",
    }
    detected = converter.detect_entity_type(doc, EntityType.CONCRETE_CFSS)
    assert detected == EntityType.CONCRETE_CFSS

    g = converter.convert(doc, EntityType.CONCRETE_CFSS)
    types = {str(o).split("#")[-1] for s, p, o in g if str(p).endswith("type")}
    assert "ConcreteCFSS" in types
    assert "GenericCFSS" not in types


def test_concrete_without_specializes_passes_shacl(converter, shacl):
    """The SHACL shape no longer requires `specializes` on a ConcreteCFSS."""
    doc = {
        "@type": "CustomerFacingServiceSpecification",
        "id": "concrete-no-spec-002",
        "name": "Another offer with no specializes",
    }
    g = converter.convert(doc, EntityType.CONCRETE_CFSS)
    uri = str(converter.entity_uri(doc, EntityType.CONCRETE_CFSS))
    shacl.validate(g, EntityType.CONCRETE_CFSS, uri)  # must not raise


def test_concrete_with_empty_relationship_list_ok(converter, shacl):
    doc = {
        "@type": "CustomerFacingServiceSpecification",
        "id": "concrete-no-spec-003",
        "name": "Offer with empty relationship list",
        "serviceSpecRelationship": [],
    }
    detected = converter.detect_entity_type(doc, EntityType.CONCRETE_CFSS)
    assert detected == EntityType.CONCRETE_CFSS
    g = converter.convert(doc, EntityType.CONCRETE_CFSS)
    uri = str(converter.entity_uri(doc, EntityType.CONCRETE_CFSS))
    shacl.validate(g, EntityType.CONCRETE_CFSS, uri)


@pytest.mark.asyncio
async def test_concrete_without_specializes_ingests(converter, shacl, json_contract):
    """End to end: a ConcreteCFSS with no references ingests with no missing-
    reference error (it has nothing to depend on)."""
    fake = FakeGraphDB(existing_uris=set())
    svc = _service(fake, converter, shacl, json_contract)
    doc = {
        "@type": "CustomerFacingServiceSpecification",
        "id": "concrete-no-spec-004",
        "name": "Standalone concrete offer",
    }
    res = await svc.ingest(doc, expected_entity_type=EntityType.CONCRETE_CFSS)
    assert res.status == "created"
    assert res.repository == get_settings().repo_concret_cfs


# --------------------------------------------------------------------------- #
# Per-type named-graph URI scheme
# --------------------------------------------------------------------------- #
def test_named_graph_uris_use_entity_type_segment(converter):
    base = get_settings().graph_base_ns

    cand = converter.entity_uri(_load("service_candidate.json"), EntityType.SERVICE_CANDIDATE)
    assert str(cand) == f"{base}service_candidate/ServiceCandidate_EMBB_001"

    con = converter.entity_uri(_load("Concret_CFSS.json"), EntityType.CONCRETE_CFSS)
    assert str(con).startswith(f"{base}concret_cfs/")

    gen = converter.entity_uri(_load("eMBB_Generic_CFSS.json"), EntityType.GENERIC_CFSS)
    assert str(gen).startswith(f"{base}generic_cfs/")

    cat = converter.top_uri(EntityType.SERVICE_CATEGORY, "SLICE_5G_001")
    assert str(cat) == f"{base}service_category/Category_SLICE_5G_001"


def test_ontology_graph_aligned():
    assert get_settings().ontology_graph == "http://vocab.nfmust.ie/tmf-service-core/schema/ontology"
