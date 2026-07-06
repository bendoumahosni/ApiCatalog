"""Ontology (TBox) management.

The ontology must be present in the repository for reasoning to apply. Under
the single-repository layout this is just one repository, but the operations
below still iterate ``settings.all_repositories`` (a one-element list) rather
than hard-coding ``settings.repository`` directly, so this module keeps
working unchanged if the deployment is ever split across more than one
repository again. The TBox is held in its own named graph
(``settings.ontology_graph``), kept separate from instance data so that
reloading it never touches an instance.

Mutating the ontology is intentionally coarse-grained (whole-graph reload)
rather than triple-level CRUD: piecemeal edits to a live TBox risk leaving
instances inconsistent. Use ``reload`` to publish a new ontology version.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from app.config import Settings
from app.deps import ONTOLOGY_PATH, settings_dep
from app.graphdb.client import get_client
from rdflib import Graph

router = APIRouter(prefix="/ontology", tags=["ontology"])


@router.get("", summary="Fetch the ontology (from the first repository) as JSON-LD")
async def get_ontology(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    repo = settings.all_repositories[0]
    graph = await get_client().construct(
        repo,
        f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{settings.ontology_graph}> {{ ?s ?p ?o }} }}",
    )
    from app.converter.ttl_to_json import graph_to_jsonld
    return {"ontology_graph": settings.ontology_graph, "triples": len(graph),
            "jsonld": graph_to_jsonld(graph)}


@router.post("/reload", summary="(Re)load the bundled ontology into all repositories")
async def reload_ontology(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    graph = Graph().parse(str(ONTOLOGY_PATH), format="turtle")
    client = get_client()
    results = {}
    for repo in settings.all_repositories:
        await client.replace_graph(repo, graph, settings.ontology_graph)
        results[repo] = len(graph)
    return {"status": "reloaded", "ontology_graph": settings.ontology_graph,
            "repositories": results}


@router.put("", summary="Replace the ontology in all repositories with a posted Turtle body")
async def put_ontology(
    turtle: str = Body(..., media_type="text/turtle"),
    settings: Settings = Depends(settings_dep),
) -> dict[str, Any]:
    graph = Graph().parse(data=turtle, format="turtle")
    client = get_client()
    results = {}
    for repo in settings.all_repositories:
        await client.replace_graph(repo, graph, settings.ontology_graph)
        results[repo] = len(graph)
    return {"status": "replaced", "ontology_graph": settings.ontology_graph,
            "repositories": results}
