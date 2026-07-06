#!/usr/bin/env python3
"""Bootstrap GraphDB for the NF-MUST Catalog API.

Single-repository layout: every TMF entity type is stored in the same
repository (``settings.repository``), each instance isolated in its own
named graph. This script still loops over ``settings.all_repositories``
(a one-element list in this layout) rather than hard-coding the repository
name, so it keeps working unchanged if the deployment is ever split across
more than one repository again. For the repository it:

  1. creates it (RDFS+ reasoning, context index) if it is missing;
  2. loads the bundled ontology into the dedicated ontology named graph.

Repository creation uses the GraphDB Workbench REST API
(``POST {base}/rest/repositories``). If your GraphDB version rejects that call,
create the repository from the Workbench UI using
``scripts/repo-config.ttl.template`` and re-run this script — the ontology load
step uses only the standard RDF4J protocol and will still work.

Usage:
    python scripts/bootstrap.py
Environment is read from .env (same variables as the API).
"""

from __future__ import annotations

import sys
from pathlib import Path
from string import Template

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY = ROOT / "ontology" / "tmf-service-core.ttl"
CONFIG_TEMPLATE = ROOT / "scripts" / "repo-config.ttl.template"


def _auth(settings):
    return (settings.graphdb_user, settings.graphdb_password or "") if settings.graphdb_user else None


def repository_exists(client: httpx.Client, settings, repo: str) -> bool:
    resp = client.get(f"{settings.graphdb_base_url}/rest/repositories")
    if resp.status_code != 200:
        # Fall back to probing the SPARQL endpoint.
        probe = client.get(settings.sparql_endpoint(repo))
        return probe.status_code < 400
    return any(item.get("id") == repo for item in resp.json())


def create_repository(client: httpx.Client, settings, repo: str) -> None:
    config = Template(CONFIG_TEMPLATE.read_text()).substitute(REPO_ID=repo)
    resp = client.post(
        f"{settings.graphdb_base_url}/rest/repositories",
        files={"config": (f"{repo}-config.ttl", config, "text/turtle")},
    )
    if resp.status_code in (200, 201):
        print(f"  created repository '{repo}'")
    else:
        print(f"  WARNING could not create '{repo}' (HTTP {resp.status_code}): "
              f"{resp.text[:200]}\n  -> create it manually from the Workbench, "
              f"then re-run.")


def load_ontology(client: httpx.Client, settings, repo: str) -> None:
    turtle = ONTOLOGY.read_text()
    # Drop then load the ontology named graph.
    client.post(
        settings.statements_endpoint(repo),
        data={"update": f"DROP SILENT GRAPH <{settings.ontology_graph}>"},
    )
    resp = client.post(
        settings.statements_endpoint(repo),
        params={"context": f"<{settings.ontology_graph}>"},
        content=turtle.encode("utf-8"),
        headers={"Content-Type": "text/turtle"},
    )
    if resp.status_code < 400:
        print(f"  loaded ontology into '{repo}' graph <{settings.ontology_graph}>")
    else:
        print(f"  ERROR loading ontology into '{repo}': HTTP {resp.status_code} {resp.text[:200]}")


def main() -> int:
    settings = get_settings()
    print(f"GraphDB: {settings.graphdb_base_url}")
    with httpx.Client(timeout=settings.http_timeout, auth=_auth(settings)) as client:
        for repo in settings.all_repositories:
            print(f"Repository '{repo}':")
            if repository_exists(client, settings, repo):
                print("  already exists")
            else:
                create_repository(client, settings, repo)
            load_ontology(client, settings, repo)
    print("Bootstrap complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
