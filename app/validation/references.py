"""Strong-reference validation.

Implements the agreed rule: only **strong** references — those pointing to a
different top-level entity than the one being ingested — are checked for
existence before an insert. Intra-unit links inside the catalog/category/
candidate group are *not* pre-validated (they form a single logical unit and
would otherwise be a chicken-and-egg problem).

Single-repository note: every entity type lives in the same GraphDB
repository now (see ``Settings.repository``), each instance isolated in its
own named graph. The ``target_repository`` field below is kept on
``StrongReference`` / reported in error payloads for diagnostic clarity (it
still tells you *which kind* of entity was expected — generic CFSS vs.
concrete CFSS vs. RFSS), even though in practice every value it can take now
resolves to the same physical repository.

Routing of a relationship to the entity kind that should hold its target is
driven by ``@referredType`` (your observation):

* ``ResourceFacingServiceSpecification`` -> RFSS
* ``CustomerFacingServiceSpecification`` -> generic CFSS (for ``specializes``)
                                             / concrete CFSS (for ``exposes``)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.config import EntityType, Settings
from app.converter.json_to_ttl import JsonToGraphConverter
from app.core.exceptions import MissingReferenceError
from app.core.logging import get_logger
from app.graphdb.client import GraphDBClient

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class StrongReference:
    """A reference whose target must already exist before this entity is
    ingested. ``target_repository`` is the repository expected to hold it —
    informational only under the single-repository layout, since every
    EntityType now resolves to the same physical repository."""

    json_field: str             # the JSON key the reference came from, e.g.
                                 # "serviceSpecification" or "serviceSpecRelationship[specializes]"
    relationship: str           # internal ontology-side label: specializes | dependsOn | exposes
    target_uri: str
    target_repository: str
    target_id: str


class ReferenceValidator:
    def __init__(
        self,
        settings: Settings,
        client: GraphDBClient,
        converter: JsonToGraphConverter,
    ) -> None:
        self._settings = settings
        self._client = client
        self._conv = converter

    # ------------------------------------------------------------------ #
    def collect(self, data: Dict[str, Any], entity_type: EntityType) -> list[StrongReference]:
        if entity_type in (EntityType.CONCRETE_CFSS, EntityType.GENERIC_CFSS,
                            EntityType.CONCRETE_RFSS, EntityType.PROFILE_MANAGEMENT):
            return self._from_spec_relationships(data)
        if entity_type == EntityType.SERVICE_CANDIDATE:
            return self._from_candidate(data)
        # Catalog / Category: intra-unit links, not pre-validated.
        return []

    def _repo_from_referred_type(self, referred_type: Any, relationship: str) -> str:
        """Resolve the repository expected to hold the reference's target.

        Kept distinct per entity kind (RFSS / generic CFSS / concrete CFSS)
        for diagnostic clarity in error payloads, even though all three
        ``repo_*`` properties now resolve to the same single repository.
        """
        rt = self._conv._norm(referred_type)
        if rt in {"resourcefacingservicespecification", "rfss"}:
            return self._settings.repo_concret_rfs
        # CustomerFacingServiceSpecification: specializes -> generic, exposes -> concrete
        if relationship == "specializes":
            return self._settings.repo_generic_cfs
        return self._settings.repo_concret_cfs

    def _from_spec_relationships(self, data: Dict[str, Any]) -> list[StrongReference]:
        refs: list[StrongReference] = []
        for rel in data.get("serviceSpecRelationship", []) or []:
            if not isinstance(rel, dict):
                continue
            rel_type = self._conv._norm(rel.get("relationshipType"))
            target = (rel.get("targetServiceSpecification") or rel.get("serviceSpec")
                      or rel.get("serviceSpecification") or {})
            if not isinstance(target, dict) or not target.get("id"):
                continue
            relationship = "specializes" if self._conv._is_specializes(rel.get("relationshipType")) \
                else ("dependsOn" if rel_type in {"dependson"} else rel_type)
            repo = self._repo_from_referred_type(target.get("@referredType"), relationship)
            # Mint the target URI under the same /<entity_type>/ segment the
            # converter will use for it, so this existence ASK hits the right
            # graph: specializes -> GenericCFSS, a RFSS @referredType -> RFSS,
            # otherwise fall back to the converter's own class detection.
            if relationship == "specializes":
                target_class = "GenericCFSS"
            elif self._conv._is_rfss(target.get("@referredType")):
                target_class = "RFSS"
            else:
                target_class = self._conv._detect_spec_class(target)
            refs.append(StrongReference(
                json_field=f"serviceSpecRelationship[relationshipType={rel.get('relationshipType')}]",
                relationship=relationship,
                target_uri=str(self._conv.spec_uri(target["id"], target_class)),
                target_repository=repo,
                target_id=str(target["id"]),
            ))
        return refs

    def _from_candidate(self, data: Dict[str, Any]) -> list[StrongReference]:
        spec = data.get("serviceSpecification")
        if not isinstance(spec, dict) or not spec.get("id"):
            return []
        # A candidate exposes exclusively a ConcreteCFSS.
        return [StrongReference(
            json_field="serviceSpecification",
            relationship="exposes",
            target_uri=str(self._conv.spec_uri(spec["id"], "ConcreteCFSS")),
            target_repository=self._settings.repo_concret_cfs,
            target_id=str(spec["id"]),
        )]

    # ------------------------------------------------------------------ #
    async def validate(self, data: Dict[str, Any], entity_type: EntityType) -> None:
        missing: list[dict[str, str]] = []
        for ref in self.collect(data, entity_type):
            exists = await self._client.ask(
                ref.target_repository,
                f"ASK WHERE {{ <{ref.target_uri}> ?p ?o }}",
            )
            if not exists:
                missing.append({
                    "json_field": ref.json_field,
                    "target_id": ref.target_id,
                    "expected_repository": ref.target_repository,
                })
        if missing:
            raise MissingReferenceError(
                "One or more referenced entities do not exist yet. Ingest them "
                "first (order: RFSS -> generic CFSS -> concrete CFSS -> "
                "catalog/category/candidate).",
                details=missing,
            )
