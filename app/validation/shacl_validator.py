"""SHACL validation of a converted instance graph.

Validation runs on the single-instance graph produced by the converter, before
anything is written to GraphDB.

Two subtleties are handled here:

1. **Only the top-level entity is validated as a full entity.** The converter
   emits typed *stubs* for relationship targets (e.g. the ConcreteCFSS behind a
   candidate's ``exposes``, or the candidate stub listed inside a category).
   Those stubs are partial by design, so the entity-level shapes must not fire
   on them. We therefore retarget the relevant entity shape to the specific
   top-node URI (``sh:targetNode``) and deactivate the other entity shapes for
   this run. Shapes for genuine children (characteristics, values) keep their
   ``sh:targetClass`` because those nodes are never cross-entity stubs.

2. **Inference is disabled.** Node types are set explicitly by the converter, so
   no reasoning is required; enabling rdfs inference would, via the ontology's
   ``rdfs:domain`` axioms, infer spurious extra types and produce false
   violations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pyshacl import validate as shacl_validate
from rdflib import Graph, Namespace, RDF, URIRef
from rdflib.namespace import SH

from app.config import EntityType
from app.core.exceptions import ValidationError
from app.core.logging import get_logger

LOGGER = get_logger(__name__)

SHAPES_NS = Namespace("http://vocab.nfmust.ie/tmf-service-core/shapes#")

# Entity-level shapes (those that can also appear as reference stubs).
_ENTITY_SHAPES: dict[EntityType, URIRef] = {
    EntityType.SERVICE_CATALOG: SHAPES_NS.ServiceCatalogShape,
    EntityType.SERVICE_CATEGORY: SHAPES_NS.CategoryShape,
    EntityType.SERVICE_CANDIDATE: SHAPES_NS.ServiceCandidateShape,
    EntityType.CONCRETE_CFSS: SHAPES_NS.ConcreteCFSSShape,
    EntityType.GENERIC_CFSS: SHAPES_NS.GenericCFSSShape,
    EntityType.CONCRETE_RFSS: SHAPES_NS.RFSShape,
}
_ALL_ENTITY_SHAPES = set(_ENTITY_SHAPES.values())


class ShaclValidator:
    def __init__(self, shapes_path: Path, ontology_path: Path) -> None:
        self._shapes_path = shapes_path
        self._ontology = Graph().parse(str(ontology_path), format="turtle")

    def _shapes_for(self, entity_type: EntityType, top_uri: str) -> Graph:
        """Build a per-run shapes graph focused on the top entity."""
        shapes = Graph().parse(str(self._shapes_path), format="turtle")
        applicable = _ENTITY_SHAPES.get(entity_type)
        for shape in _ALL_ENTITY_SHAPES:
            # Remove class-based targeting from every entity shape.
            shapes.remove((shape, SH.targetClass, None))
            if shape == applicable:
                shapes.add((shape, SH.targetNode, URIRef(top_uri)))
            else:
                shapes.add((shape, SH.deactivated, _true()))
        return shapes

    def validate(
        self,
        data_graph: Graph,
        entity_type: EntityType,
        top_uri: str,
    ) -> None:
        shapes = self._shapes_for(entity_type, top_uri)
        conforms, _report_graph, report_text = shacl_validate(
            data_graph,
            shacl_graph=shapes,
            inference="none",
            advanced=True,
            meta_shacl=False,
        )
        if not conforms:
            raise ValidationError(
                "SHACL validation failed.",
                details=self._summarise(report_text),
            )

    @staticmethod
    def _summarise(report_text: str) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        block: dict[str, Any] = {}
        for line in report_text.splitlines():
            s = line.strip()
            if s.startswith("Constraint Violation"):
                if block:
                    violations.append(block)
                block = {}
            elif s.startswith("Message:"):
                block["message"] = s.split("Message:", 1)[1].strip()
            elif s.startswith("Focus Node:"):
                block["focus_node"] = s.split("Focus Node:", 1)[1].strip()
            elif s.startswith("Result Path:"):
                block["path"] = s.split("Result Path:", 1)[1].strip()
        if block:
            violations.append(block)
        return violations or [{"message": report_text[:1000]}]


def _true():
    from rdflib import Literal
    from rdflib.namespace import XSD
    return Literal(True, datatype=XSD.boolean)
