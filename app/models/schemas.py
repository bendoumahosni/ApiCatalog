"""API request/response models."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.config import EntityType


class IngestResult(BaseModel):
    status: str = Field(examples=["created", "replaced"])
    entity_type: EntityType
    repository: str
    entity_uri: str
    named_graph: str
    triples_written: int


class ValidationReport(BaseModel):
    conforms: bool
    entity_type: Optional[EntityType] = None
    entity_uri: Optional[str] = None
    json_contract_violations: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    missing_references: list[dict[str, str]] = []


class ConcreteCFSSHit(BaseModel):
    concret: str
    name: Optional[str] = None
    description: Optional[str] = None


class QueryResult(BaseModel):
    count: int
    federated: bool
    results: list[dict[str, Any]]


class HealthStatus(BaseModel):
    api: str = "ok"
    graphdb_base_url: str
    repositories: dict[str, bool]


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    details: Any | None = None
