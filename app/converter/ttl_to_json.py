"""Graph -> JSON reconstruction for read endpoints.

GET endpoints return the stored representation as **JSON-LD**, obtained directly
from the RDF graph fetched from GraphDB. JSON-LD is chosen over a hand-written
TTL->JSON mapper because it is lossless, standards-based, and requires no
bespoke per-type code that could drift from the ingestion mapping.
"""

from __future__ import annotations

import json
from typing import Any

from rdflib import Graph

from app.config import get_settings


def graph_to_jsonld(graph: Graph) -> list[dict[str, Any]]:
    """Serialize a graph to a compacted JSON-LD structure."""
    settings = get_settings()
    context = {
        "tmf": settings.ontology_ns,
        "ex": settings.instance_ns,
    }
    raw = graph.serialize(format="json-ld", context=context, auto_compact=True)
    parsed = json.loads(raw)
    # json-ld may return a dict with @graph or a bare list; normalise to a list.
    if isinstance(parsed, dict):
        return parsed.get("@graph", [parsed])
    return parsed
