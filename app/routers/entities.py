"""Shared request models for the catalog discovery / multi-criteria search.

These were originally declared next to a generic ``/{kind}`` CRUD router, but
that router's literal "/concret-cfs/filter" and "/concret-cfs/search" paths
were permanently shadowed by ``entities_explicit/concret_cfs.py``'s
``/concret-cfs/{entity_id}`` catch-all -- the two routers were both mounted
in ``app/main.py``, and Starlette resolves against the flattened route table
in registration order across *all* included routers, not router-by-router.
Whichever router was included first always won for any path matching
"/concret-cfs/<anything>".

The actual ``/concret-cfs/filter`` and ``/concret-cfs/search`` endpoints now
live directly in ``entities_explicit/concret_cfs.py``, registered on that
same router via ``ExplicitEntityRoute.register_before`` -- *before* its
``{entity_id}`` route is added, which is the only ordering that reliably
avoids the shadowing. The generic ``/{kind}`` CRUD handlers that used to live
in this module were exact duplicates of what ``entities_explicit/*`` already
provides for all 7 known entity types (same factory, same behavior) and are
removed rather than kept dead and unreachable.

``SearchCriterion``/``SearchRequest`` remain here, shared between this
docstring's history and ``concret_cfs.py``, which imports them.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SearchCriterion(BaseModel):
    characteristic: str = Field(..., description="Characteristic name, e.g. 'Availability'")
    value: str = Field(..., description="Required value, e.g. 'High' or '5000000'")
    scope: Literal["local", "generic"] = Field(
        "local",
        description=(
            "'local' if this characteristic is defined on the ConcreteCFSS itself "
            "(no extra join); 'generic' if it is only defined on the GenericCFSS "
            "parent (matched in the same query via a join across named graphs)."
        ),
    )


class SearchRequest(BaseModel):
    criteria: list[SearchCriterion] = Field(..., min_length=1)
    catalog: Optional[str] = Field(None, description="Optional catalog name filter")
    category: Optional[str] = Field(None, description="Optional category name filter")
    limit: int = Field(100, ge=1, le=1000)
