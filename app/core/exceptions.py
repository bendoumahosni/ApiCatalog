"""Domain exceptions and their HTTP mapping.

Keeping these in one place lets routers stay thin: services raise a meaningful
domain error and a single exception handler turns it into the right HTTP
status with a consistent JSON body.
"""

from __future__ import annotations

from typing import Any, Optional


class CatalogAPIError(Exception):
    """Base class for all expected, user-facing errors."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, *, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class EntityTypeDetectionError(CatalogAPIError):
    status_code = 422
    error_code = "entity_type_detection_failed"


class EntityTypeMismatchError(CatalogAPIError):
    """Raised when a document is posted to an entity-specific route (e.g.
    POST /service-catalog) but its detected TMF type does not match what that
    route expects. Explicit routing means the route declares the contract;
    a document of a different type is a client error, not something to
    silently re-route."""

    status_code = 422
    error_code = "entity_type_mismatch"


class ValidationError(CatalogAPIError):
    """Raised when SHACL or structural validation fails."""

    status_code = 422
    error_code = "validation_failed"


class MissingReferenceError(CatalogAPIError):
    """Raised when a strong cross-repository reference does not exist yet."""

    status_code = 409
    error_code = "missing_reference"


class EntityAlreadyExistsError(CatalogAPIError):
    status_code = 409
    error_code = "entity_already_exists"


class EntityNotFoundError(CatalogAPIError):
    status_code = 404
    error_code = "entity_not_found"


class GraphDBError(CatalogAPIError):
    """Raised when GraphDB returns an unexpected response."""

    status_code = 502
    error_code = "graphdb_error"
