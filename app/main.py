"""FastAPI application entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8000

Interactive docs at /docs once running.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.core.exceptions import CatalogAPIError
from app.core.logging import configure_logging, get_logger
from app.deps import init_singletons
from app.graphdb.client import init_client, get_client
from app.routers import ingestion, ontology, system
from app.routers.entities_explicit import ALL_ROUTERS as EXPLICIT_ENTITY_ROUTERS

LOGGER = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    LOGGER.info("Starting NF-MUST Catalog API against GraphDB %s", settings.graphdb_base_url)
    init_singletons()
    init_client(settings)
    try:
        yield
    finally:
        await get_client().aclose()
        LOGGER.info("Shutdown complete.")


app = FastAPI(
    title="NF-MUST Catalog API",
    version="1.0.0",
    description=(
        "Ingests TMF633 JSON documents, converts them to RDF conforming to the "
        "TMF service-core ontology, validates them  "
        "and stores each instance in its own named graph in "
        "the appropriate GraphDB repository. Also exposes the catalog discovery "
        "queries and ontology management."
    ),
    lifespan=lifespan,
)

# CORS: allows the frontend (e.g. the Vite dev server on a different port,
# hence a different browser "origin") to call this API. Without this, every
# request from the browser fails at the network layer before reaching any
# route -- which looks indistinguishable, from the frontend's point of view,
# from the API being unreachable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(CatalogAPIError)
async def domain_error_handler(_: Request, exc: CatalogAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": exc.error_code, "message": exc.message, "details": exc.details},
    )


app.include_router(system.router)
app.include_router(ingestion.router)
app.include_router(ontology.router)
# Each of these 7 routers handles one TMF entity type's full CRUD plus, for
# concret-cfs, the /filter and /search discovery endpoints -- registered in
# the right order internally (see entities_explicit/_factory.py). There is
# no separate generic "/{kind}" catch-all router: it used to exist
# (app/routers/entities.py) but was an exact, permanently-unreachable
# duplicate of what these 7 already provide.
for _router in EXPLICIT_ENTITY_ROUTERS:
    app.include_router(_router)
