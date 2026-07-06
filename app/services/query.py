"""SPARQL query construction for the catalog discovery use case.

The catalog discovery use case is: find ConcreteCFSS matching one or more
criteria, where each criterion is a (characteristic name, value) pair that may
live either on the ConcreteCFSS itself (local, e.g. an SLA-specific override)
or on its GenericCFSS parent (generic), and where catalog/category filters
narrow the candidate set.

Two building blocks:

* :func:`build_concrete_cfss_query` — discovery filtered by catalog/category
  and any number of *local* characteristics on the ConcreteCFSS itself.

* :func:`build_combined_query` — the general case. Accepts any number of
  criteria, each tagged with its scope ("local" or "generic"). Local criteria
  are matched directly against the ConcreteCFSS; generic criteria are matched
  against its GenericCFSS parent. This is what powers a request like
  "Availability=High AND throughput=5000000 AND isolation=Physical" regardless
  of whether a characteristic happens to be recorded on the concrete spec or
  only on its generic parent.

Single-repository note: every entity type (catalog, category, candidate,
generic CFSS, concrete CFSS, RFSS) is stored in the same GraphDB repository,
each instance isolated in its own named graph. GraphDB's default query scope
spans every named graph within a repository, so joining a ConcreteCFSS to its
GenericCFSS parent across two different named graphs is an ordinary SPARQL
join — no ``SERVICE`` federation is needed (that was only required under the
earlier four-repository layout, where the generic parent lived in a separate
repository).

Design note shared by every characteristic filter below: a (name, value) pair
is always written as one connected blank-node block
(``tmf:hasCharacteristic [ tmf:name "..." ; tmf:hasCharacteristicValue
[ tmf:value "..." ] ]``) rather than as two separately-named variables joined
by shared variable names. This matters most when a "local" and a "generic"
criterion target nodes in different named graphs in the same query: some
SPARQL execution paths evaluate triple patterns close to independently and
join results afterwards, which can silently decouple a "name" constraint from
the "value" constraint that was meant to apply to the very same
characteristic node, letting an unrelated node holding the right value "leak"
through. Blank nodes make that decoupling syntactically impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from app.config import Settings

_PREFIX = "PREFIX tmf: <http://vocab.nfmust.ie/tmf-service-core#>\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _char_block(subject: str, characteristic: str, value: str, var_prefix: str, indent: str = "  ") -> list[str]:
    """One connected blank-node block constraining `subject` to have a
    characteristic `characteristic` whose recorded value, compared as TEXT,
    equals `value`.

    Why STR() instead of a typed-literal triple match: the converter types
    `tmf:value` according to the *native JSON type* of the source value — a
    JSON number becomes `xsd:integer`/`xsd:decimal`, a JSON string becomes a
    plain (untyped) literal. Two TMF633 documents can therefore describe the
    "same" characteristic value with different JSON types (e.g. one feed
    writes `"value": 1000000` while another writes `"value": "1000000"`), and
    a caller of this API has no visibility into which form ended up in
    GraphDB. An exact triple-pattern match (`tmf:value "1000000"`) only
    matches a plain-literal "1000000" and silently misses an
    `xsd:integer 1000000` — which looks identical to a human reading the JSON
    but is a different RDF term. Comparing `STR(?v) = "value"` compares the
    lexical form regardless of datatype, so the filter behaves the same
    whichever way the source JSON happened to type the number.

    `var_prefix` must be unique per call site within the same query so that
    criteria placed in the same scope don't accidentally share a value
    variable.
    """
    return [
        f"{indent}{subject} tmf:hasCharacteristic [",
        f'{indent}  tmf:name "{_escape(characteristic)}" ;',
        f"{indent}  tmf:hasCharacteristicValue [",
        f"{indent}    tmf:value ?{var_prefix}_v",
        f"{indent}  ]",
        f"{indent}] .",
        f'{indent}FILTER(STR(?{var_prefix}_v) = "{_escape(value)}")',
    ]


def build_concrete_cfss_query(
    *,
    catalog_name: Optional[str] = None,
    category_name: Optional[str] = None,
    characteristic: Optional[str] = None,
    char_value: Optional[str] = None,
    limit: int = 100,
) -> str:
    """Discovery query for concrete CFSS, filtered by catalog/category and/or
    a local characteristic on the ConcreteCFSS itself."""
    lines = [
        _PREFIX,
        "SELECT DISTINCT ?concret ?id ?name ?description WHERE {",
        "  ?concret a tmf:ConcreteCFSS ;",
        "           tmf:name ?name .",
        "  OPTIONAL { ?concret tmf:id ?id . }",
        "  OPTIONAL { ?concret tmf:description ?description . }",
    ]
    if catalog_name or category_name:
        lines += [
            "  ?candidate tmf:exposes ?concret ;",
            "             tmf:hasCategory ?category .",
        ]
        if category_name:
            lines.append(f'  ?category tmf:name "{_escape(category_name)}" .')
        if catalog_name:
            lines += [
                "  ?catalog a tmf:ServiceCatalog ;",
                "           tmf:organizedInto ?category ;",
                f'           tmf:name "{_escape(catalog_name)}" .',
            ]
    if characteristic and char_value:
        lines += _char_block("?concret", characteristic, char_value, var_prefix="c0")
    lines.append(f"}} LIMIT {int(limit)}")
    return "\n".join(lines)


def build_federated_generic_query(
    settings: Settings,
    *,
    criteria: list[tuple[str, str]],
    limit: int = 100,
) -> str:
    """ConcreteCFSS joined to its GenericCFSS parent's characteristics,
    filtered by one or more (name, value) criteria, all of which must be
    satisfied (AND).

    Historical note: this used to issue a ``SERVICE <repository:generic_CFS>``
    federated call because the generic CFSS lived in a separate GraphDB
    repository. Now that every entity type is stored in the same repository
    (each instance still isolated in its own named graph), the generic
    parent's characteristics are matched with an ordinary triple pattern —
    GraphDB queries across named graphs within one repository by default, no
    federation needed. ``settings`` is kept as a parameter for call-site
    compatibility even though it is no longer used to target a different
    repository.
    """
    if not criteria:
        raise ValueError("At least one (characteristic, value) criterion is required.")

    lines = [
        _PREFIX,
        "SELECT DISTINCT ?concret ?name WHERE {",
        "  ?concret a tmf:ConcreteCFSS ;",
        "           tmf:name ?name ;",
        "           tmf:specializes ?generic .",
    ]
    for i, (characteristic, value) in enumerate(criteria):
        lines += _char_block("?generic", characteristic, value, var_prefix=f"g{i}", indent="  ")
    lines.append(f"}} LIMIT {int(limit)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Combined multi-criteria search (the general case).
# --------------------------------------------------------------------------- #

CriterionScope = Literal["local", "generic"]


@dataclass(frozen=True)
class Criterion:
    """One (characteristic, value) constraint, tagged with where it lives.

    scope="local"   -> the characteristic is on the ConcreteCFSS itself.
    scope="generic" -> the characteristic is on the GenericCFSS parent
                        (its own named graph, same repository).
    """

    characteristic: str
    value: str
    scope: CriterionScope = "local"


def build_combined_query(
    settings: Settings,
    *,
    criteria: list[Criterion],
    catalog_name: Optional[str] = None,
    category_name: Optional[str] = None,
    limit: int = 100,
) -> str:
    """General discovery query: any number of criteria, each local or generic,
    combined with AND, plus optional catalog/category narrowing.

    Example — "Availability=High AND throughput=5000000 AND isolation=Physical"
    where Availability happens to be overridden locally on the ConcreteCFSS but
    throughput and isolation are only defined on the GenericCFSS parent:

        build_combined_query(settings, criteria=[
            Criterion("Availability", "High", scope="local"),
            Criterion("Guaranteed downlink throughput quota", "5000000", scope="generic"),
            Criterion("Isolation", "Physical", scope="generic"),
        ])
    """
    local_criteria = [c for c in criteria if c.scope == "local"]
    generic_criteria = [c for c in criteria if c.scope == "generic"]

    lines = [
        _PREFIX,
        "SELECT DISTINCT ?concret ?name WHERE {",
        "  ?concret a tmf:ConcreteCFSS ;",
        "           tmf:name ?name .",
    ]

    needs_candidate = bool(catalog_name or category_name)
    if needs_candidate or generic_criteria:
        lines.append("  ?concret tmf:specializes ?generic .") if generic_criteria else None
    if needs_candidate:
        lines += [
            "  ?candidate tmf:exposes ?concret ;",
            "             tmf:hasCategory ?category .",
        ]
        if category_name:
            lines.append(f'  ?category tmf:name "{_escape(category_name)}" .')
        if catalog_name:
            lines += [
                "  ?catalog a tmf:ServiceCatalog ;",
                "           tmf:organizedInto ?category ;",
                f'           tmf:name "{_escape(catalog_name)}" .',
            ]

    for i, c in enumerate(local_criteria):
        lines += _char_block("?concret", c.characteristic, c.value, var_prefix=f"l{i}")

    if generic_criteria:
        # Same-repository join: no SERVICE federation needed anymore (see
        # build_federated_generic_query docstring) — the generic parent's
        # characteristics live in their own named graph inside the same
        # repository and are matched with a plain triple pattern.
        for i, c in enumerate(generic_criteria):
            lines += _char_block("?generic", c.characteristic, c.value, var_prefix=f"g{i}")

    lines.append(f"}} LIMIT {int(limit)}")
    return "\n".join(lines)
