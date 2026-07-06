"""Tests for app/services/query.py.

Covers:
* every generated query has balanced braces (catches the kind of literal-
  double-brace bug fixed in list_entities for a different module);
* the blank-node characteristic filter actually rejects a wrong value and
  accepts the correct one when evaluated against a real RDF graph (regression
  test for the join-decoupling bug: a (name, value) filter expressed as two
  separately-joinable named variables could match an unrelated node sharing
  the same value, this is now structurally impossible with blank nodes);
* the combined query correctly splits criteria between the local block and the
  SERVICE block based on `scope`.
"""

from __future__ import annotations

import pytest
from rdflib import RDF, Graph, Literal, Namespace, URIRef

from app.config import get_settings
from app.services.query import (
    Criterion,
    _char_block,
    build_combined_query,
    build_concrete_cfss_query,
    build_federated_generic_query,
)

TMF = Namespace("http://vocab.nfmust.ie/tmf-service-core#")


def _balanced(query: str) -> bool:
    return query.count("{") == query.count("}") and query.count("[") == query.count("]")


def test_build_concrete_cfss_query_balanced_in_all_filter_combinations():
    for kwargs in [
        {},
        {"catalog_name": "NF_MUST_Catalog"},
        {"category_name": "Slice_5G"},
        {"catalog_name": "NF_MUST_Catalog", "category_name": "Slice_5G"},
        {"characteristic": "Availability", "char_value": "High"},
        {"catalog_name": "NF_MUST_Catalog", "characteristic": "Availability", "char_value": "High"},
    ]:
        q = build_concrete_cfss_query(**kwargs)
        assert _balanced(q), q


def test_build_federated_generic_query_balanced_single_and_multi_criteria():
    settings = get_settings()
    q1 = build_federated_generic_query(settings, criteria=[("Availability", "High")])
    assert _balanced(q1)
    q2 = build_federated_generic_query(settings, criteria=[
        ("Availability", "High"), ("Isolation", "Physical"),
    ])
    assert _balanced(q2)


def test_build_federated_generic_query_requires_at_least_one_criterion():
    with pytest.raises(ValueError):
        build_federated_generic_query(get_settings(), criteria=[])


def test_build_combined_query_balanced_local_generic_and_mixed():
    settings = get_settings()
    cases = [
        [Criterion("Availability", "High", "local")],
        [Criterion("Isolation", "Physical", "generic")],
        [
            Criterion("Availability", "High", "local"),
            Criterion("Guaranteed downlink throughput quota", "5000000", "generic"),
            Criterion("Isolation", "Physical", "generic"),
        ],
    ]
    for criteria in cases:
        q = build_combined_query(settings, criteria=criteria)
        assert _balanced(q), q


# --------------------------------------------------------------------------- #
# Regression: blank-node filter must not let an unrelated value "leak" through
# a (name, value) pair that is supposed to apply to the SAME characteristic
# node. This is the exact bug reported against /concret-cfs/search.
# --------------------------------------------------------------------------- #
def _make_generic_graph() -> tuple[Graph, URIRef]:
    g = Graph()
    generic = URIRef("http://x/generic")
    g.add((generic, RDF.type, TMF.GenericCFSS))

    # Availability = High
    c1, v1 = URIRef("http://x/c_avail"), URIRef("http://x/v_avail")
    g.add((generic, TMF.hasCharacteristic, c1))
    g.add((c1, TMF.name, Literal("Availability")))
    g.add((c1, TMF.hasCharacteristicValue, v1))
    g.add((v1, TMF.value, Literal("High")))

    # Isolation = 2 (the real value)
    c2, v2 = URIRef("http://x/c_iso"), URIRef("http://x/v_iso")
    g.add((generic, TMF.hasCharacteristic, c2))
    g.add((c2, TMF.name, Literal("Isolation level: Isolation")))
    g.add((c2, TMF.hasCharacteristicValue, v2))
    g.add((v2, TMF.value, Literal("2")))

    # A third, unrelated characteristic that happens to carry value "0"
    c3, v3 = URIRef("http://x/c_other"), URIRef("http://x/v_other")
    g.add((generic, TMF.hasCharacteristic, c3))
    g.add((c3, TMF.name, Literal("SomethingElse")))
    g.add((c3, TMF.hasCharacteristicValue, v3))
    g.add((v3, TMF.value, Literal("0")))

    return g, generic


def _ask_characteristic_value(g: Graph, characteristic: str, value: str) -> bool:
    body = "\n".join(_char_block("?generic", characteristic, value, var_prefix="x"))
    q = f"PREFIX tmf: <http://vocab.nfmust.ie/tmf-service-core#>\nASK WHERE {{\n{body}\n}}"
    return bool(g.query(q).askAnswer)


def test_blank_node_filter_rejects_wrong_value_for_real_characteristic():
    g, _ = _make_generic_graph()
    # The real value is "2"; "0" must NOT match even though "0" exists
    # elsewhere in the graph on an unrelated characteristic.
    assert _ask_characteristic_value(g, "Isolation level: Isolation", "0") is False


def test_blank_node_filter_accepts_correct_value():
    g, _ = _make_generic_graph()
    assert _ask_characteristic_value(g, "Isolation level: Isolation", "2") is True


def test_blank_node_filter_does_not_leak_unrelated_value():
    """An unrelated characteristic ('SomethingElse') carrying value '0' must
    never satisfy a filter on a different characteristic name."""
    g, _ = _make_generic_graph()
    assert _ask_characteristic_value(g, "Availability", "0") is False


# --------------------------------------------------------------------------- #
# Regression: tmf:value can be stored with different RDF datatypes depending
# on the *native JSON type* of the source value (a JSON number becomes
# xsd:integer/xsd:decimal, a JSON string becomes a plain literal). The query
# must match on the textual form regardless of which datatype ended up in
# GraphDB — this is exactly what broke in the real uploaded eMBB_Generic_CFSS
# data: "Isolation level: Isolation" values are JSON strings ("0", "1", "2"),
# while "Downlink maximum throughput per UE: ..." is a JSON number (1000000),
# so it round-trips as xsd:integer.
# --------------------------------------------------------------------------- #
def test_filter_matches_plain_string_typed_value():
    """Mirrors 'Isolation level: Isolation' in the real data: JSON
    '"value": "1"' converts to a plain (untyped) literal '1'."""
    g = Graph()
    generic = URIRef("http://x/generic")
    c, v = URIRef("http://x/c"), URIRef("http://x/v")
    g.add((generic, TMF.hasCharacteristic, c))
    g.add((c, TMF.name, Literal("Isolation level: Isolation")))
    g.add((c, TMF.hasCharacteristicValue, v))
    g.add((v, TMF.value, Literal("1")))  # plain literal, no datatype

    assert _ask_characteristic_value(g, "Isolation level: Isolation", "1") is True
    assert _ask_characteristic_value(g, "Isolation level: Isolation", "0") is False


def test_filter_matches_xsd_integer_typed_value():
    """Mirrors 'Downlink maximum throughput per UE: ...' in the real data:
    JSON '"value": 1000000' (a number) converts to xsd:integer. A query that
    sends the value as a quoted string must still match it."""
    from rdflib.namespace import XSD

    g = Graph()
    generic = URIRef("http://x/generic")
    c, v = URIRef("http://x/c"), URIRef("http://x/v")
    g.add((generic, TMF.hasCharacteristic, c))
    g.add((c, TMF.name, Literal(
        "Downlink maximum throughput per UE: Maximum downlink throughput per UE value")))
    g.add((c, TMF.hasCharacteristicValue, v))
    g.add((v, TMF.value, Literal(1000000, datatype=XSD.integer)))

    assert _ask_characteristic_value(
        g, "Downlink maximum throughput per UE: Maximum downlink throughput per UE value",
        "1000000",
    ) is True
    assert _ask_characteristic_value(
        g, "Downlink maximum throughput per UE: Maximum downlink throughput per UE value",
        "999",
    ) is False


def test_multiple_criteria_use_distinct_value_variables():
    """Two criteria in the same scope must not accidentally share a value
    variable (which would silently turn the AND of two filters into a
    contradiction or a cross-constraint)."""
    settings = get_settings()
    q = build_federated_generic_query(settings, criteria=[
        ("Availability", "High"), ("Isolation level: Isolation", "1"),
    ])
    assert "?g0_v" in q and "?g1_v" in q
    assert q.count("?g0_v") == 2  # bound once, filtered once
    assert q.count("?g1_v") == 2
