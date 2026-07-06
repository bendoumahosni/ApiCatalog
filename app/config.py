"""Application configuration.

All settings are read from environment variables (prefix ``NFMUST_``) or a
local ``.env`` file. Nothing about the deployment target is hard-coded: moving
from the development box (``localhost:7200``) to production
(``137.194.70.195:7200``) is a single environment-variable change.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class EntityType(str, Enum):
    """The seven TMF document types accepted by the API."""

    SERVICE_CATALOG = "service_catalog"
    SERVICE_CATEGORY = "service_category"
    SERVICE_CANDIDATE = "service_candidate"
    CONCRETE_CFSS = "concret_cfs"
    GENERIC_CFSS = "generic_cfs"
    CONCRETE_RFSS = "concret_rfs"
    PROFILE_MANAGEMENT = "service_profile_management"


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_prefix="NFMUST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- GraphDB connection ---
    graphdb_base_url: str = "http://localhost:7200"
    graphdb_user: Optional[str] = None
    graphdb_password: Optional[str] = None
    http_timeout: float = 30.0

    # --- Repository identifier ---
    # Single-repository architecture: every TMF entity type (catalog,
    # category, candidate, generic CFSS, concrete CFSS, RFSS, management
    # profile) lives in the SAME GraphDB repository, each instance isolated
    # in its own named graph (see JsonToGraphConverter.entity_uri /
    # ``entity_uri == named_graph``). This keeps reasoning (rdfs:subClassOf,
    # transitive dependsOn/requires/hasSubCategory) consistent across the
    # whole graph and lets every cross-type traversal (catalog ->
    # category -> candidate -> concrete CFSS -> specializes -> generic CFSS
    # -> dependsOn -> RFSS) run as a single, ordinary SPARQL query instead of
    # a federated ``SERVICE`` query across repositories.
    #
    # The four ``repo_*`` aliases below are kept (all pointing at the same
    # repository) purely so existing call sites that distinguish "the
    # repository that holds generic CFSS" from "the repository that holds
    # concrete CFSS" etc. keep working unchanged — they simply all resolve to
    # one place now.
    repository: str = "nfmust_catalog"

    @property
    def repo_concret_cfs(self) -> str:
        return self.repository

    @property
    def repo_generic_cfs(self) -> str:
        return self.repository

    @property
    def repo_concret_rfs(self) -> str:
        return self.repository

    @property
    def repo_profile(self) -> str:
        return self.repository

    # --- Namespaces ---
    # Named-graph / top-level-entity URIs are minted as
    #   {graph_base_ns}{entity_type.value}/{local}
    # e.g. http://vocab.nfmust.ie/tmf-service-core/service_candidate/ServiceCandidate_EMBB_001
    # The segment is the EntityType enum value, so the graph layout in GraphDB
    # mirrors the entity types one-to-one. Each top-level entity URI is ALSO
    # the name of the named graph that holds it (graph == subject), which keeps
    # read/replace/delete a single DROP/PUT on that one graph.
    #
    # Sub-entities that only exist inside a parent's graph (characteristics,
    # characteristic values, time periods, SLS stubs, constraints, related
    # parties, reified relationships) keep the dedicated ``instance_ns`` below:
    # they are never addressed as graphs of their own, so their URI scheme is
    # irrelevant to the graph layout and is left untouched.
    graph_base_ns: str = "http://vocab.nfmust.ie/tmf-service-core/"
    ontology_graph: str = "http://vocab.nfmust.ie/tmf-service-core/schema/ontology"
    instance_ns: str = "http://vocab.nfmust.ie/tmf-service-core/instance/"
    ontology_ns: str = "http://vocab.nfmust.ie/tmf-service-core#"

    # --- Behaviour ---
    shacl_enabled: bool = True
    # Strong-reference existence checks before a write enforce an ingestion
    # order (RFSS -> generic CFSS -> concrete CFSS -> catalog/category/
    # candidate) by refusing a document whose referenced entities are not in
    # the store yet. Disabled by default: bulk/automated ingestion (e.g.
    # mirroring an external OpenSlice catalogue) does not control the order in
    # which documents arrive, so the order must not be enforced. Set to true to
    # re-enable the checks.
    reference_checks_enabled: bool = False
    log_level: str = "INFO"

    # --- CORS (frontend access) ---
    # Comma-separated list of origins allowed to call this API from a browser
    # (e.g. the Vite dev server). "*" disables credentialed requests per the
    # CORS spec, so explicit origins are used instead, with sensible local
    # defaults covering Vite's default port and 0.0.0.0/127.0.0.1 variants.
    cors_allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    # ------------------------------------------------------------------ #
    # Derived helpers
    # ------------------------------------------------------------------ #
    @property
    def entity_to_repository(self) -> dict[EntityType, str]:
        """Routing table: which repository stores each entity type.

        Single-repository architecture: every entity type resolves to the
        same repository. Each entity is still fully isolated by its own
        named graph (see ``IngestionService`` / ``JsonToGraphConverter``),
        so per-entity create/read/replace/delete behave exactly as before —
        only the repository they happen to share has changed, from four
        repositories down to one.
        """
        return {entity_type: self.repository for entity_type in EntityType}

    def repository_for(self, entity_type: EntityType) -> str:
        return self.entity_to_repository[entity_type]

    @property
    def all_repositories(self) -> list[str]:
        return [self.repository]

    def sparql_endpoint(self, repository: str) -> str:
        return f"{self.graphdb_base_url.rstrip('/')}/repositories/{repository}"

    def statements_endpoint(self, repository: str) -> str:
        return f"{self.sparql_endpoint(repository)}/statements"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor used as a FastAPI dependency."""
    return Settings()
