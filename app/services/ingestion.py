"""Ingestion orchestration — the end-to-end pipeline.

Steps, in order:

1. Detect the TMF document type (``@type`` with heuristic fallback).
2. Resolve the target repository (single repository shared by every entity
   type — see ``Settings.repository``).
3. Convert JSON -> RDF graph (in memory).
4. SHACL-validate the converted graph (structural + business rules), focused on
   the top entity.
5. Validate strong references exist (ASK queries).
6. Write the graph into a **named graph** (= the entity URI), isolated from
   every other entity even though they now share one repository.
   ``create`` refuses if the graph already exists; ``replace`` (PUT) drops then
   re-inserts atomically.
"""

from __future__ import annotations

from typing import Any, Dict

from app.config import EntityType, Settings
from app.converter.json_to_ttl import JsonToGraphConverter
from app.core.exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityTypeMismatchError,
)
from app.core.logging import get_logger
from app.graphdb.client import GraphDBClient
from app.models.schemas import IngestResult, ValidationReport
from app.validation.json_contract import JsonContractValidator
from app.validation.references import ReferenceValidator
from app.validation.shacl_validator import ShaclValidator

LOGGER = get_logger(__name__)


class IngestionService:
    def __init__(
        self,
        settings: Settings,
        client: GraphDBClient,
        converter: JsonToGraphConverter,
        shacl: ShaclValidator,
        json_contract: JsonContractValidator,
    ) -> None:
        self._settings = settings
        self._client = client
        self._conv = converter
        self._shacl = shacl
        self._json_contract = json_contract
        self._refs = ReferenceValidator(settings, client, converter)

    def _check_expected_type(
        self, data: Dict[str, Any], detected: EntityType, expected: EntityType | None
    ) -> None:
        """Enforced by entity-specific routes (e.g. POST /service-catalog),
        which route *explicitly* rather than dynamically: the route itself
        declares which TMF type it accepts, and a document detected as a
        different type is rejected outright rather than silently stored under
        whatever type its own @type/heuristics implied."""
        if expected is not None and detected != expected:
            raise EntityTypeMismatchError(
                f"This endpoint only accepts '{expected.value}' documents, but the "
                f"posted document was detected as '{detected.value}'.",
                details={"expected_entity_type": expected.value, "detected_entity_type": detected.value},
            )

    # ------------------------------------------------------------------ #
    async def dry_run(
        self, data: Dict[str, Any], *, expected_entity_type: EntityType | None = None
    ) -> ValidationReport:
        """Validate without writing (powers POST /validate)."""
        entity_type = self._conv.detect_entity_type(data, expected_entity_type)
        self._check_expected_type(data, entity_type, expected_entity_type)

        report = ValidationReport(conforms=True, entity_type=entity_type, entity_uri=None)

        # Step 1: JSON contract (raw input, real JSON keys). If this fails,
        # the document is malformed at the source and conversion is skipped —
        # there is no point validating RDF derived from a bad input.
        from app.core.exceptions import ValidationError as _VErr
        try:
            self._json_contract.validate(data, entity_type)
        except _VErr as exc:
            report.conforms = False
            report.json_contract_violations = exc.details or []
            return report

        graph = self._conv.convert(data, entity_type)
        uri = str(self._conv.entity_uri(data, entity_type))
        report.entity_uri = uri

        # Step 2: SHACL on the converted graph (structure + business rules).
        if self._settings.shacl_enabled:
            try:
                self._shacl.validate(graph, entity_type, uri)
            except _VErr as exc:
                report.conforms = False
                report.violations = exc.details or []

        # Step 3: strong-reference existence (ingestion-order enforcement).
        # Disabled by default — see Settings.reference_checks_enabled.
        if self._settings.reference_checks_enabled:
            from app.core.exceptions import MissingReferenceError
            try:
                await self._refs.validate(data, entity_type)
            except MissingReferenceError as exc:
                report.conforms = False
                report.missing_references = exc.details or []
        return report

    # ------------------------------------------------------------------ #
    async def ingest(
        self,
        data: Dict[str, Any],
        *,
        replace: bool = False,
        expected_entity_type: EntityType | None = None,
    ) -> IngestResult:
        entity_type = self._conv.detect_entity_type(data, expected_entity_type)
        self._check_expected_type(data, entity_type, expected_entity_type)

        # Step 1: JSON contract — fail fast on a malformed/misnamed input.
        self._json_contract.validate(data, entity_type)

        repository = self._settings.repository_for(entity_type)
        graph = self._conv.convert(data, entity_type)
        named_graph = str(self._conv.entity_uri(data, entity_type))

        # Step 2: SHACL on the converted graph.
        if self._settings.shacl_enabled:
            self._shacl.validate(graph, entity_type, named_graph)

        # Step 3: strong-reference existence (ingestion-order enforcement).
        # Disabled by default — see Settings.reference_checks_enabled.
        if self._settings.reference_checks_enabled:
            await self._refs.validate(data, entity_type)

        exists = await self._client.graph_exists(repository, named_graph)
        if exists and not replace:
            raise EntityAlreadyExistsError(
                f"Entity already exists at <{named_graph}>. Use PUT to replace it.",
                details={"entity_uri": named_graph, "repository": repository},
            )
        if replace and not exists:
            # PUT on a non-existing entity behaves as create (upsert).
            LOGGER.info("PUT on non-existing graph <%s>; creating.", named_graph)

        if replace:
            await self._client.replace_graph(repository, graph, named_graph)
            status = "replaced"
        else:
            await self._client.add_graph(repository, graph, named_graph)
            status = "created"

        return IngestResult(
            status=status,
            entity_type=entity_type,
            repository=repository,
            entity_uri=named_graph,
            named_graph=named_graph,
            triples_written=len(graph),
        )

    # ------------------------------------------------------------------ #
    async def get_named_graph(self, repository: str, named_graph: str):
        if not await self._client.graph_exists(repository, named_graph):
            raise EntityNotFoundError(
                f"No entity found at <{named_graph}> in repository '{repository}'.",
                details={"entity_uri": named_graph},
            )
        return await self._client.construct(
            repository,
            f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{named_graph}> {{ ?s ?p ?o }} }}",
        )

    async def delete_named_graph(self, repository: str, named_graph: str) -> None:
        if not await self._client.graph_exists(repository, named_graph):
            raise EntityNotFoundError(
                f"No entity found at <{named_graph}> in repository '{repository}'.",
                details={"entity_uri": named_graph},
            )
        await self._client.drop_graph(repository, named_graph)

    async def list_entities(self, repository: str, rdf_class: str, limit: int = 100):
        query = (
            "PREFIX tmf: <http://vocab.nfmust.ie/tmf-service-core#>\n"
            f"SELECT ?uri ?id ?name WHERE {{\n"
            f"  ?uri a tmf:{rdf_class} .\n"
            f"  OPTIONAL {{ ?uri tmf:id ?id }}\n"
            f"  OPTIONAL {{ ?uri tmf:name ?name }}\n"
            f"}}\n"
            f"LIMIT {int(limit)}"
        )
        rows = await self._client.select(repository, query)
        return rows
