"""Async GraphDB client.

Talks to GraphDB through the RDF4J REST protocol that GraphDB implements:

* SPARQL SELECT / ASK / CONSTRUCT  -> POST {base}/repositories/{repo}
* SPARQL UPDATE (INSERT/DELETE/DROP) -> POST {base}/repositories/{repo}/statements
* Bulk RDF load into a named graph  -> POST RDF body with ?context=<graph>

A single ``httpx.AsyncClient`` is reused for the whole process (created on
startup, closed on shutdown).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from rdflib import Graph

from app.config import Settings
from app.core.exceptions import GraphDBError
from app.core.logging import get_logger

LOGGER = get_logger(__name__)

_SPARQL_JSON = "application/sparql-results+json"


class GraphDBClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        auth = None
        if settings.graphdb_user:
            auth = (settings.graphdb_user, settings.graphdb_password or "")
        self._client = httpx.AsyncClient(timeout=settings.http_timeout, auth=auth)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #
    async def select(self, repository: str, query: str) -> list[dict[str, Any]]:
        """Run a SELECT query and return a list of {var: value} rows."""
        resp = await self._client.post(
            self._settings.sparql_endpoint(repository),
            data={"query": query},
            headers={"Accept": _SPARQL_JSON},
        )
        self._raise_for_status(resp, "SELECT")
        payload = resp.json()
        out: list[dict[str, Any]] = []
        for binding in payload.get("results", {}).get("bindings", []):
            out.append({k: v.get("value") for k, v in binding.items()})
        return out

    async def ask(self, repository: str, query: str) -> bool:
        resp = await self._client.post(
            self._settings.sparql_endpoint(repository),
            data={"query": query},
            headers={"Accept": _SPARQL_JSON},
        )
        self._raise_for_status(resp, "ASK")
        return bool(resp.json().get("boolean", False))

    async def construct(self, repository: str, query: str) -> Graph:
        resp = await self._client.post(
            self._settings.sparql_endpoint(repository),
            data={"query": query},
            headers={"Accept": "text/turtle"},
        )
        self._raise_for_status(resp, "CONSTRUCT")
        g = Graph()
        g.parse(data=resp.text, format="turtle")
        return g

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #
    async def update(self, repository: str, sparql_update: str) -> None:
        resp = await self._client.post(
            self._settings.statements_endpoint(repository),
            data={"update": sparql_update},
        )
        self._raise_for_status(resp, "UPDATE")

    async def add_graph(self, repository: str, graph: Graph, named_graph: str) -> None:
        """Bulk-load triples into a named graph (RDF4J ?context= load)."""
        turtle = graph.serialize(format="turtle")
        resp = await self._client.post(
            self._settings.statements_endpoint(repository),
            params={"context": f"<{named_graph}>"},
            content=turtle.encode("utf-8"),
            headers={"Content-Type": "text/turtle"},
        )
        self._raise_for_status(resp, "ADD GRAPH")

    async def drop_graph(self, repository: str, named_graph: str) -> None:
        await self.update(repository, f"DROP SILENT GRAPH <{named_graph}>")

    async def replace_graph(self, repository: str, graph: Graph, named_graph: str) -> None:
        """Idempotent replace: drop then load, used by PUT."""
        await self.drop_graph(repository, named_graph)
        await self.add_graph(repository, graph, named_graph)

    async def graph_exists(self, repository: str, named_graph: str) -> bool:
        return await self.ask(
            repository, f"ASK WHERE {{ GRAPH <{named_graph}> {{ ?s ?p ?o }} }}"
        )

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    async def repository_health(self, repository: str) -> bool:
        try:
            await self.ask(repository, "ASK WHERE { ?s ?p ?o }")
            return True
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Repository '%s' not reachable: %s", repository, exc)
            return False

    # ------------------------------------------------------------------ #
    def _raise_for_status(self, resp: httpx.Response, op: str) -> None:
        if resp.status_code >= 400:
            raise GraphDBError(
                f"GraphDB {op} failed with HTTP {resp.status_code}",
                details=resp.text[:500],
            )


_client: Optional[GraphDBClient] = None


def init_client(settings: Settings) -> GraphDBClient:
    global _client
    _client = GraphDBClient(settings)
    return _client


def get_client() -> GraphDBClient:
    if _client is None:
        raise RuntimeError("GraphDB client not initialised.")
    return _client
