"""JSON (TMF633) -> RDF graph conversion.

This module is a refactor of the project's original ``generate_service_spec_ttl.py``
script. Two deliberate changes were made relative to that script:

1. **Top-level instance URIs are minted from the ``id`` alone** (not
   ``name_id``). Joins across entity types (e.g. a concrete CFSS joined to its
   generic parent, each living in its own named graph) match on the URI;
   minting on the id removes the risk of a silent mismatch when a reference
   spells the ``name`` slightly differently from the canonical entity.

2. **The specification exposed by a ``ServiceCandidate`` is typed
   ``ConcreteCFSS``** (the original script defaulted it to ``GenericCFSS``).
   Business rule: a candidate exposes *exclusively* a concrete CFSS, so the
   stub created for the ``exposes`` target must carry that type — otherwise the
   same URI ends up with two conflicting types once the real concrete CFSS is
   ingested.

The converter is intentionally **named-graph-agnostic**: it returns a flat
``rdflib.Graph``. The ingestion layer decides the named graph (the entity URI)
when writing to GraphDB. This keeps the converter reusable for validation-only
flows.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL, XSD

from app.config import EntityType, get_settings
from app.core.exceptions import EntityTypeDetectionError, ValidationError
from app.core.logging import get_logger

LOGGER = get_logger(__name__)


def slugify(value: Any, fallback: str = "item") -> str:
    if value is None:
        value = fallback
    text = str(value).strip() or fallback
    text = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def _literal(value: Any) -> Optional[Literal]:
    if value is None:
        return None
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, float):
        return Literal(value, datatype=XSD.decimal)
    if isinstance(value, Decimal):
        return Literal(value, datatype=XSD.decimal)
    return Literal(str(value))


class JsonToGraphConverter:
    """Convert a single TMF JSON document into an rdflib Graph."""

    def __init__(self, ontology_path: Path) -> None:
        settings = get_settings()
        self.ontology_ns = Namespace(settings.ontology_ns)
        self.instance_ns = Namespace(settings.instance_ns)
        self.graph_base_ns = Namespace(settings.graph_base_ns)

        self._ontology = Graph()
        self._ontology.parse(str(ontology_path), format="turtle")
        self.classes = self._local_names(OWL.Class)
        self.object_properties = self._local_names(OWL.ObjectProperty)
        self.data_properties = self._local_names(OWL.DatatypeProperty)

    # ------------------------------------------------------------------ #
    # Ontology helpers
    # ------------------------------------------------------------------ #
    def _local_names(self, rdf_type: URIRef) -> set[str]:
        out: set[str] = set()
        for subject in self._ontology.subjects(RDF.type, rdf_type):
            out.add(str(subject).split("#")[-1].split("/")[-1])
        return out

    def _term(self, local: str) -> URIRef:
        return self.ontology_ns[local]

    def inst(self, local: str) -> URIRef:
        return self.instance_ns[slugify(local)]

    def _resolve_class(self, class_name: str) -> str:
        # The project says "RFSS"; the ontology declares the class as "RFS".
        if class_name == "RFSS" and "RFSS" not in self.classes and "RFS" in self.classes:
            return "RFS"
        return class_name

    def _add_type(self, g: Graph, subj: URIRef, class_name: str) -> None:
        resolved = self._resolve_class(class_name)
        if resolved in self.classes:
            g.add((subj, RDF.type, self._term(resolved)))
        else:
            LOGGER.warning("Class '%s' missing from ontology; type skipped.", resolved)

    def _add_data(self, g: Graph, subj: URIRef, prop: str, value: Any) -> None:
        if value is None or prop not in self.data_properties:
            return
        lit = _literal(value)
        if lit is not None:
            g.add((subj, self._term(prop), lit))

    def _add_object(self, g: Graph, subj: URIRef, prop: str, obj: URIRef) -> None:
        if prop in self.object_properties:
            g.add((subj, self._term(prop), obj))

    def _new_graph(self) -> Graph:
        g = Graph()
        g.bind("tmf", self.ontology_ns)
        g.bind("ex", self.instance_ns)
        g.bind("owl", OWL)
        g.bind("rdfs", RDFS)
        g.bind("xsd", XSD)
        return g

    # ------------------------------------------------------------------ #
    # URI minting
    # ------------------------------------------------------------------ #
    # Top-level entity (= named-graph) URIs are minted as
    #   {graph_base_ns}{entity_type.value}/{local}
    # The local part keeps the historical, human-readable form (an id-only
    # local for specs, a "ServiceCatalog_<id>"/"Category_<id>"/
    # "ServiceCandidate_<id>" local for the catalog trio), so only the
    # namespace segment changes relative to the previous "/instance/" scheme.
    #
    # graph == subject: the value returned here is BOTH the RDF subject of the
    # entity and the name of the named graph that stores it.

    # RDF class (as used by _add_type, after _resolve_class) -> EntityType,
    # so a spec referenced only by class (a relationship target, the exposes
    # stub) lands under the same /<entity_type>/ segment as when it is ingested
    # in its own right. This is what keeps cross-entity joins
    # (?candidate tmf:exposes ?concret, ?concret tmf:specializes ?generic)
    # resolving to the same URI on both sides.
    _SPEC_CLASS_TO_ENTITY = {
        "ConcreteCFSS": EntityType.CONCRETE_CFSS,
        "GenericCFSS": EntityType.GENERIC_CFSS,
        "RFS": EntityType.CONCRETE_RFSS,
        "RFSS": EntityType.CONCRETE_RFSS,
        "ServiceManagementProfile": EntityType.PROFILE_MANAGEMENT,
    }

    def _graph_uri(self, entity_type: EntityType, local: str) -> URIRef:
        """Mint a top-level entity / named-graph URI under the per-type segment."""
        return self.graph_base_ns[f"{entity_type.value}/{slugify(local)}"]

    def spec_uri(self, spec_id: Any, spec_class: str = "GenericCFSS") -> URIRef:
        """URI of a ServiceSpecification (CFSS/RFSS/profile) — id-only local,
        placed under the segment implied by its RDF class.

        ``spec_class`` is the class that will be (or has been) asserted on the
        spec via ``_add_type``; it only selects the namespace segment and never
        changes the local part, so the same id always yields the same URI for a
        given class regardless of where it is referenced from.
        """
        resolved = self._resolve_class(spec_class)
        entity_type = self._SPEC_CLASS_TO_ENTITY.get(resolved, EntityType.GENERIC_CFSS)
        return self._graph_uri(entity_type, str(spec_id))

    def top_uri(self, entity_type: EntityType, entity_id: Any) -> URIRef:
        prefix = {
            EntityType.SERVICE_CATALOG: "ServiceCatalog_",
            EntityType.SERVICE_CATEGORY: "Category_",
            EntityType.SERVICE_CANDIDATE: "ServiceCandidate_",
        }.get(entity_type, "")
        if prefix:
            return self._graph_uri(entity_type, f"{prefix}{entity_id}")
        # CFSS / RFSS / profile: id-only local under the type segment.
        return self._graph_uri(entity_type, str(entity_id))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def detect_entity_type(
        self, data: Dict[str, Any], expected: Optional[EntityType] = None
    ) -> EntityType:
        """Detect the TMF document type from ``@type`` (with heuristic fallback).

        ``expected`` is the type declared by an explicit route (e.g.
        ``POST /concret-cfs`` -> ``CONCRETE_CFSS``). When the document is a
        customer-facing service specification, the Concrete-vs-Generic
        distinction is taken from ``expected`` if provided, and only otherwise
        inferred from the presence of a ``specializes`` relationship. This is
        what lets a ConcreteCFSS be ingested with an empty
        ``serviceSpecRelationship`` (or one that does not contain
        ``specializes``): the route says it is concrete, so it is treated as
        concrete -- the historical "no specializes => generic" inference is a
        fallback for the type-agnostic ``/validate`` path only.
        """
        raw = re.sub(r"[^a-z0-9]", "", str(data.get("@type") or "").lower())
        if raw == "servicecatalog":
            return EntityType.SERVICE_CATALOG
        if raw in ("servicecategory", "category"):
            return EntityType.SERVICE_CATEGORY
        if raw == "servicecandidate":
            return EntityType.SERVICE_CANDIDATE
        if raw in ("resourcefacingservicespecification", "rfss"):
            return EntityType.CONCRETE_RFSS
        if raw in ("servicemanagementprofile", "managementprofile"):
            return EntityType.PROFILE_MANAGEMENT
        if raw in ("customerfacingservicespecification", "cfss", "servicespecification"):
            return self._disambiguate_cfss(data, expected)

        # Fallback heuristics for documents without a reliable @type.
        if "serviceCandidate" in data or "organizedInto" in data:
            return EntityType.SERVICE_CATALOG
        if isinstance(data.get("serviceSpecification"), dict) and "category" in data:
            return EntityType.SERVICE_CANDIDATE
        if "isRoot" in data or isinstance(data.get("serviceCandidate"), list):
            return EntityType.SERVICE_CATEGORY

        blob = " ".join(
            str(v) for v in (data.get("href"), data.get("name"), data.get("id")) if v
        ).lower()
        if "rfss" in blob or "resourcefacing" in blob:
            return EntityType.CONCRETE_RFSS
        if "cfss" in blob or "customerfacing" in blob:
            return self._disambiguate_cfss(data, expected)
        # A CFSS-looking document with neither @type nor textual hints: trust
        # the route if it told us which CFSS flavour it expects.
        if expected in (EntityType.CONCRETE_CFSS, EntityType.GENERIC_CFSS):
            return expected
        # Last-resort autonomous detection (e.g. the type-agnostic /validate
        # path with no @type and no route hint): a document that specializes
        # another spec is a ConcreteCFSS. This is only a fallback for inferring
        # the type of an OTHERWISE-unidentifiable document; it is NOT a
        # requirement -- a ConcreteCFSS posted to its explicit route is accepted
        # with no specializes at all (see _disambiguate_cfss).
        if self._has_specializes(data):
            return EntityType.CONCRETE_CFSS

        raise EntityTypeDetectionError(
            "Cannot determine the TMF document type. Set '@type' explicitly "
            "(e.g. 'ServiceCatalog', 'ServiceCandidate', "
            "'CustomerFacingServiceSpecification', "
            "'ResourceFacingServiceSpecification')."
        )

    def _disambiguate_cfss(
        self, data: Dict[str, Any], expected: Optional[EntityType]
    ) -> EntityType:
        """Concrete vs Generic CFSS.

        Prefers the route-declared ``expected`` type; falls back to the
        presence of a ``specializes`` relationship only when no CFSS type was
        declared. A ConcreteCFSS no longer needs a ``specializes`` relationship
        to be recognised as concrete.
        """
        if expected in (EntityType.CONCRETE_CFSS, EntityType.GENERIC_CFSS):
            return expected
        return (
            EntityType.CONCRETE_CFSS
            if self._has_specializes(data)
            else EntityType.GENERIC_CFSS
        )

    def entity_uri(self, data: Dict[str, Any], entity_type: EntityType) -> URIRef:
        """Top-level instance URI (also used as the named-graph name)."""
        entity_id = data.get("id")
        if not entity_id:
            raise ValidationError("Document is missing the mandatory 'id' field.")
        return self.top_uri(entity_type, entity_id)

    def convert(self, data: Dict[str, Any], entity_type: EntityType) -> Graph:
        dispatch = {
            EntityType.SERVICE_CATALOG: self._convert_catalog,
            EntityType.SERVICE_CATEGORY: self._convert_category,
            EntityType.SERVICE_CANDIDATE: self._convert_candidate,
        }
        handler = dispatch.get(entity_type, self._convert_service_spec)
        return handler(data, entity_type)

    # ------------------------------------------------------------------ #
    # ServiceSpecification (Generic / Concrete CFSS, RFSS)
    # ------------------------------------------------------------------ #
    def _has_specializes(self, data: Dict[str, Any]) -> bool:
        for rel in data.get("serviceSpecRelationship", []) or []:
            if isinstance(rel, dict) and self._is_specializes(rel.get("relationshipType")):
                return True
        return False

    @staticmethod
    def _norm(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def _is_specializes(self, rel_type: Any) -> bool:
        return self._norm(rel_type) in {
            "specializes", "specialise", "specialises",
            "specialization", "specialisation",
            "isspecializationof", "isspecialisationof",
        }

    def _is_realized_by(self, rel_type: Any) -> bool:
        return self._norm(rel_type) in {
            "isrealizedby", "realizedby", "isrealisedby", "realisedby",
        }

    def _is_rfss(self, value: Any) -> bool:
        return self._norm(value) in {
            "rfss", "resourcefacingservicespecification", "resourcefacingservicespec",
        }

    def _is_cfss(self, value: Any) -> bool:
        return self._norm(value) in {
            "cfss", "customerfacingservicespecification", "customerfacingservicespec",
        }

    def _detect_spec_class(self, data: Dict[str, Any]) -> str:
        t, rt = data.get("@type"), data.get("@referredType")
        if self._is_rfss(t) or self._is_rfss(rt):
            return "RFSS"
        if self._is_cfss(t) or self._is_cfss(rt):
            return "ConcreteCFSS" if self._has_specializes(data) else "GenericCFSS"
        if self._has_specializes(data):
            return "ConcreteCFSS"
        blob = " ".join(
            str(v) for v in (data.get("href"), data.get("name"), data.get("id")) if v
        ).lower()
        if "rfss" in blob or "resourcefacing" in blob:
            return "RFSS"
        return "GenericCFSS"

    # EntityType -> RDF class asserted on the spec subject. The reverse of
    # _SPEC_CLASS_TO_ENTITY for the three ServiceSpecification flavours plus
    # the management profile.
    _ENTITY_TO_SPEC_CLASS = {
        EntityType.CONCRETE_CFSS: "ConcreteCFSS",
        EntityType.GENERIC_CFSS: "GenericCFSS",
        EntityType.CONCRETE_RFSS: "RFSS",
        EntityType.PROFILE_MANAGEMENT: "ServiceManagementProfile",
    }

    def _convert_service_spec(self, data: Dict[str, Any], entity_type: EntityType) -> Graph:
        if not data.get("id") or not data.get("name"):
            raise ValidationError("ServiceSpecification requires both 'id' and 'name'.")

        g = self._new_graph()
        # Trust the resolved entity_type (set by detect_entity_type, which
        # honours the route's expected type) rather than re-inferring from a
        # `specializes` relationship: a ConcreteCFSS stays concrete even with
        # no serviceSpecRelationship at all.
        spec_class = self._ENTITY_TO_SPEC_CLASS.get(entity_type) or self._detect_spec_class(data)
        uri = self.spec_uri(data["id"], spec_class)
        self._add_type(g, uri, spec_class)

        for prop in ("id", "name", "description", "version", "href", "isBundle", "lifecycleStatus"):
            self._add_data(g, uri, prop, data.get(prop))

        self._map_valid_for(g, uri, data.get("validFor"), f"{data['id']}_validFor")
        self._map_characteristics(g, uri, data["id"], data.get("serviceSpecCharacteristic", []))
        self._map_spec_relationships(g, uri, data, data.get("serviceSpecRelationship", []))
        self._map_resources(g, uri, data["id"], data.get("resourceSpecification", []))
        self._map_slss(g, uri, data["id"], data.get("serviceLevelSpecification", []))
        self._map_constraints(g, uri, data["id"], data.get("constraint", []))
        self._map_parties(g, uri, data["id"], data.get("relatedParty", []))
        return g

    # ------------------------------------------------------------------ #
    # serviceSpecRelationship
    # ------------------------------------------------------------------ #
    def _map_spec_relationships(
        self, g: Graph, source_uri: URIRef, source: Dict[str, Any], rels: Any
    ) -> None:
        if not rels:
            return
        if not isinstance(rels, list):
            raise ValidationError("'serviceSpecRelationship' must be a list.")

        source_id = source.get("id") or "source"
        for i, rel in enumerate(rels, start=1):
            if not isinstance(rel, dict):
                continue
            target = rel.get("targetServiceSpecification") or rel.get("serviceSpec") \
                or rel.get("serviceSpecification")
            if not isinstance(target, dict):
                # Flat / self-describing form (OpenSlice): the target id, name
                # and href are carried directly on the relationship object,
                # with no nested wrapper. Reconstruct a target reference from
                # those fields so the link is preserved instead of dropped.
                if rel.get("id") or rel.get("name"):
                    target = {
                        "id": rel.get("id"),
                        "name": rel.get("name"),
                        "href": rel.get("href"),
                        "@type": rel.get("@type"),
                        "@referredType": rel.get("@referredType"),
                    }
                else:
                    continue

            target_uri = self._create_target_spec(g, target, rel.get("relationshipType"))
            rel_uri = self.inst(f"{source_id}_rel_{i}")
            self._add_type(g, rel_uri, "ServiceSpecRelationship")
            self._add_object(g, source_uri, "hasServiceSpecRelationship", rel_uri)
            self._add_object(g, rel_uri, "targetServiceSpecification", target_uri)
            self._add_data(g, rel_uri, "relationshipType", rel.get("relationshipType"))
            self._add_data(g, rel_uri, "role", rel.get("role"))
            self._add_data(g, rel_uri, "id", rel.get("id"))
            self._add_data(g, rel_uri, "name", rel.get("name"))

            shortcut = self._semantic_property(rel.get("relationshipType"))
            if shortcut:
                self._add_object(g, source_uri, shortcut, target_uri)

    def _semantic_property(self, rel_type: Any) -> Optional[str]:
        mapping = {
            "specializes": "specializes", "specialise": "specializes",
            "specialises": "specializes", "specialization": "specializes",
            "specialisation": "specializes",
            "dependson": "dependsOn", "requires": "requires", "require": "requires",
            "isrealizedby": "isRealizedBy", "realizedby": "isRealizedBy",
        }
        prop = mapping.get(self._norm(rel_type))
        return prop if prop and prop in self.object_properties else None

    def _create_target_spec(self, g: Graph, target: Dict[str, Any], rel_type: Any) -> URIRef:
        target_id = target.get("id") or slugify(target.get("name"), "target")
        if self._is_specializes(rel_type):
            target_class = "GenericCFSS"
        elif self._is_realized_by(rel_type):
            # isRealizedBy : ontology range is RFS (a ConcreteCFSS is realized
            # by resource-facing specifications), so the target is an RFSS even
            # when its name/href do not literally contain "rfss".
            target_class = "RFSS"
        else:
            target_class = self._detect_spec_class(target)
        target_uri = self.spec_uri(target_id, target_class)
        self._add_type(g, target_uri, target_class)

        for prop in ("id", "name", "version", "href", "description", "lifecycleStatus"):
            self._add_data(g, target_uri, prop, target.get(prop))
        return target_uri

    # ------------------------------------------------------------------ #
    # Characteristics
    # ------------------------------------------------------------------ #
    def _map_characteristics(self, g: Graph, spec_uri: URIRef, spec_id: Any, chars: Any) -> None:
        if not chars:
            return
        if not isinstance(chars, list):
            raise ValidationError("'serviceSpecCharacteristic' must be a list.")
        for ci, char in enumerate(chars, start=1):
            if not isinstance(char, dict):
                continue
            name = char.get("name") or f"Characteristic_{ci}"
            cu = self.inst(f"{spec_id}_char_{ci}_{name}")
            self._add_type(g, cu, "CharacteristicSpecification")
            self._add_object(g, spec_uri, "hasCharacteristic", cu)
            for prop in ("name", "description", "configurable", "extensible",
                         "isUnique", "maxCardinality", "minCardinality", "regex", "valueType"):
                self._add_data(g, cu, prop, char.get(prop))
            self._map_char_tags(g, cu, char.get("serviceSpecCharRelationship", []))
            self._map_char_values(g, cu, spec_id, ci, name,
                                  char.get("serviceSpecCharacteristicValue", []))

    def _map_char_tags(self, g: Graph, cu: URIRef, rels: Any) -> None:
        if not isinstance(rels, list):
            return
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            self._add_data(g, cu, "tag", rel.get("name"))
            self._add_data(g, cu, "role", rel.get("role"))
            self._add_data(g, cu, "relationshipType", rel.get("relationshipType"))

    def _map_char_values(self, g, cu, spec_id, ci, cname, values) -> None:
        if not isinstance(values, list):
            return
        for vi, vobj in enumerate(values, start=1):
            if not isinstance(vobj, dict):
                continue
            vu = self.inst(f"{spec_id}_char_{ci}_val_{vi}_{cname}")
            self._add_type(g, vu, "ServiceSpecCharacteristicValue")
            self._add_object(g, cu, "hasCharacteristicValue", vu)
            for prop in ("isDefault", "rangeInterval", "regex", "unitOfMeasure", "valueType"):
                self._add_data(g, vu, prop, vobj.get(prop))
            # valueFrom/valueTo are xsd:decimal in the ontology. Use Decimal
            # (not float): a float-backed literal is treated as ill-typed for
            # xsd:decimal by SHACL processors.
            for prop in ("valueFrom", "valueTo"):
                raw = vobj.get(prop)
                self._add_data(g, vu, prop, None if raw is None else Decimal(str(raw)))
            nested = vobj.get("value")
            if isinstance(nested, dict):
                self._add_data(g, vu, "value", nested.get("value"))
                self._add_data(g, vu, "valueAlias", nested.get("alias"))
            else:
                self._add_data(g, vu, "value", nested)
            self._map_valid_for(g, vu, vobj.get("validFor"),
                                f"{spec_id}_char_{ci}_val_{vi}_validFor")

    # ------------------------------------------------------------------ #
    # Nested mappings
    # ------------------------------------------------------------------ #
    def _map_valid_for(self, g: Graph, parent: URIRef, vf: Any, local_id: str) -> None:
        if not isinstance(vf, dict):
            return
        vu = self.inst(f"TimePeriod_{local_id}")
        self._add_type(g, vu, "TimePeriod")
        self._add_object(g, parent, "validFor", vu)
        self._add_data(g, vu, "startDateTime", vf.get("startDateTime"))
        self._add_data(g, vu, "endDateTime", vf.get("endDateTime"))

    def _map_resources(self, g, spec_uri, spec_id, resources) -> None:
        if not isinstance(resources, list):
            return
        for i, r in enumerate(resources, start=1):
            if not isinstance(r, dict):
                continue
            ru = self.inst(f"{spec_id}_resource_{r.get('id') or r.get('name') or i}")
            self._add_type(g, ru, "ResourceSpecification")
            self._add_object(g, spec_uri, "requireResourceSpecification", ru)
            for prop in ("id", "href", "name", "version", "description"):
                self._add_data(g, ru, prop, r.get(prop))

    def _map_slss(self, g, spec_uri, spec_id, slss) -> None:
        if not isinstance(slss, list):
            return
        for i, s in enumerate(slss, start=1):
            if not isinstance(s, dict):
                continue
            su = self.inst(f"{spec_id}_sls_{s.get('id') or s.get('name') or i}")
            self._add_type(g, su, "ServiceLevelSpecification")
            self._add_object(g, spec_uri, "hasServiceLevelSpecification", su)
            for prop in ("id", "href", "name", "description"):
                self._add_data(g, su, prop, s.get(prop))

    def _map_constraints(self, g, spec_uri, spec_id, constraints) -> None:
        if not isinstance(constraints, list):
            return
        for i, c in enumerate(constraints, start=1):
            if not isinstance(c, dict):
                continue
            cu = self.inst(f"{spec_id}_constraint_{c.get('id') or c.get('name') or i}")
            self._add_type(g, cu, "Constraint")
            self._add_object(g, spec_uri, "hasConstraint", cu)
            for prop in ("id", "name", "description"):
                self._add_data(g, cu, prop, c.get(prop))
            # 'value'/'expression' if present
            self._add_data(g, cu, "value", c.get("value"))
            self._add_data(g, cu, "expression", c.get("expression"))

    def _map_parties(self, g, spec_uri, spec_id, parties) -> None:
        if not isinstance(parties, list):
            return
        for i, p in enumerate(parties, start=1):
            if not isinstance(p, dict):
                continue
            pu = self.inst(f"{spec_id}_party_{p.get('id') or p.get('name') or i}")
            self._add_type(g, pu, "RelatedParty")
            self._add_object(g, spec_uri, "hasRelatedParty", pu)
            for prop in ("id", "href", "name", "role"):
                self._add_data(g, pu, prop, p.get(prop))

    # ------------------------------------------------------------------ #
    # ServiceCatalog
    # ------------------------------------------------------------------ #
    def _convert_catalog(self, data: Dict[str, Any], _: EntityType) -> Graph:
        g = self._new_graph()
        cid = data.get("id")
        uri = self.top_uri(EntityType.SERVICE_CATALOG, cid)
        self._add_type(g, uri, "ServiceCatalog")
        for prop in ("id", "name", "description", "version", "href", "lifecycleStatus"):
            self._add_data(g, uri, prop, data.get(prop))
        self._map_valid_for(g, uri, data.get("validFor"), f"ServiceCatalog_{cid}_validFor")
        self._map_parties(g, uri, f"ServiceCatalog_{cid}", data.get("relatedParty", []))
        for ref in data.get("category", []) or []:
            if not isinstance(ref, dict):
                continue
            cat_uri = self.top_uri(EntityType.SERVICE_CATEGORY, ref.get("id") or ref.get("name"))
            self._add_type(g, cat_uri, "Category")
            for prop in ("id", "name", "version", "href"):
                self._add_data(g, cat_uri, prop, ref.get(prop))
            self._add_object(g, uri, "organizedInto", cat_uri)
        return g

    # ------------------------------------------------------------------ #
    # ServiceCategory
    # ------------------------------------------------------------------ #
    def _convert_category(self, data: Dict[str, Any], _: EntityType) -> Graph:
        g = self._new_graph()
        cid = data.get("id")
        uri = self.top_uri(EntityType.SERVICE_CATEGORY, cid)
        self._add_type(g, uri, "Category")
        for prop in ("id", "name", "description", "version", "href", "lifecycleStatus"):
            self._add_data(g, uri, prop, data.get(prop))
        self._map_valid_for(g, uri, data.get("validFor"), f"Category_{cid}_validFor")
        for sub in data.get("category", []) or []:
            if not isinstance(sub, dict):
                continue
            sub_uri = self.top_uri(EntityType.SERVICE_CATEGORY, sub.get("id") or sub.get("name"))
            self._add_type(g, sub_uri, "Category")
            for prop in ("id", "name", "version", "href"):
                self._add_data(g, sub_uri, prop, sub.get(prop))
            self._add_object(g, uri, "hasSubCategory", sub_uri)
        for cand in data.get("serviceCandidate", []) or []:
            if not isinstance(cand, dict):
                continue
            cand_uri = self.top_uri(EntityType.SERVICE_CANDIDATE, cand.get("id") or cand.get("name"))
            self._add_type(g, cand_uri, "ServiceCandidate")
            for prop in ("id", "name", "version", "href"):
                self._add_data(g, cand_uri, prop, cand.get(prop))
            self._add_object(g, uri, "contains", cand_uri)
        return g

    # ------------------------------------------------------------------ #
    # ServiceCandidate
    # ------------------------------------------------------------------ #
    def _convert_candidate(self, data: Dict[str, Any], _: EntityType) -> Graph:
        g = self._new_graph()
        cid = data.get("id")
        uri = self.top_uri(EntityType.SERVICE_CANDIDATE, cid)
        self._add_type(g, uri, "ServiceCandidate")
        for prop in ("id", "name", "description", "version", "href", "lifecycleStatus"):
            self._add_data(g, uri, prop, data.get(prop))
        self._map_valid_for(g, uri, data.get("validFor"), f"ServiceCandidate_{cid}_validFor")
        for ref in data.get("category", []) or []:
            if not isinstance(ref, dict):
                continue
            cat_uri = self.top_uri(EntityType.SERVICE_CATEGORY, ref.get("id") or ref.get("name"))
            self._add_type(g, cat_uri, "Category")
            for prop in ("id", "name", "version", "href"):
                self._add_data(g, cat_uri, prop, ref.get(prop))
            self._add_object(g, uri, "hasCategory", cat_uri)

        # serviceSpecification -> exposes. A candidate exposes EXCLUSIVELY a
        # ConcreteCFSS, so the stub is typed ConcreteCFSS (the agreed fix).
        spec = data.get("serviceSpecification")
        if isinstance(spec, dict):
            spec_uri = self.spec_uri(spec.get("id") or spec.get("name"), "ConcreteCFSS")
            self._add_type(g, spec_uri, "ConcreteCFSS")
            for prop in ("id", "name", "version", "href"):
                self._add_data(g, spec_uri, prop, spec.get(prop))
            self._add_object(g, uri, "exposes", spec_uri)
        return g