# NF-MUST Catalog API

A FastAPI backend that populates a TMF service-core ontology with **instances**
and stores them in **GraphDB**. The flow is exactly the one we converged on:

```
JSON (TMF633)  →  validate   →  RDF/Turtle  →  named graph in GraphDB
```

It also exposes the catalog **discovery queries** and **ontology 
management**.

---

## 1. Design in one screen

**One repository, one named graph per instance.** Every TMF entity type
(catalog, category, candidate, generic CFSS, concrete CFSS, RFSS, management
profile) is stored in the **same** GraphDB repository (`nfmust_catalog`). 

**Two complementary validations before any write.**
* **SHACL** (`shapes/shapes.ttl`) checks structure and business rules per entity
  type — e.g. *a candidate exposes exactly one ConcreteCFSS*, *a GenericCFSS
  specializes nothing*, *if a ConcreteCFSS specializes something it must be a
  GenericCFSS*. 
* **Strong reference checks** (ASK queries) can verify that referenced entities
  already exist, routed by `@referredType`
  (`ResourceFacingServiceSpecification` → RFSS,
  `CustomerFacingServiceSpecification` → generic/concrete CFSS). **These checks
  are disabled by default**  so that
  bulk/automated ingestion need not control arrival order; 

---

## 2. Project layout

```
nfmust-catalog-api/
├── app/
│   ├── main.py                 # FastAPI app + lifespan + error handler
│   ├── config.py               # Settings, EntityType, routing table
│   ├── deps.py                 # singletons (converter, SHACL) + DI providers
│   ├── core/                   # logging, domain exceptions
│   ├── converter/
│   │   ├── json_to_ttl.py      # JSON → rdflib Graph (your script, refactored)
│   │   └── ttl_to_json.py      # Graph → JSON-LD for GET
│   ├── graphdb/client.py       # async RDF4J/GraphDB client (httpx)
│   ├── validation/
│   │   ├── shacl_validator.py  # pyshacl, top-entity-focused
│   │   └── references.py       # cross-repo strong-reference checks
│   ├── services/
│   │   ├── ingestion.py        # the orchestration pipeline
│   │   └── query.py            # local + federated SPARQL builders
│   ├── models/schemas.py       # Pydantic request/response models
│   └── routers/
│       ├── entities_explicit/  # one file per TMF entity, explicit CRUD routes
│       │   ├── _factory.py       # shared route-building logic (not mounted itself)
│       │   ├── service_catalog.py, service_category.py, service_candidate.py
│       │   ├── generic_cfs.py, concret_cfs.py, concret_rfs.py, service_profile.py
│       │   └── __init__.py       # ALL_ROUTERS / ROUTE_SPECS, mounted in main.py
│       ├── entities.py          # shared SearchCriterion/SearchRequest models
│       ├── ingestion.py, ontology.py, system.py (health/ready, validate, query)
├── ontology/tmf-service-core.ttl
├── shapes/shapes.ttl
├── examples/                   # the provided sample documents
├── scripts/                    # bootstrap.py, ingest_examples.py, repo config
├── tests/                      # pytest (offline + fake-GraphDB pipeline)
├── Dockerfile                  # multi-stage, non-root, gunicorn, healthcheck
├── docker-entrypoint.sh        # optional bootstrap, then exec the server
├── gunicorn_conf.py            # production server config (env-overridable)
├── docker-compose.yml          # API + GraphDB
├── Makefile, requirements.txt, .env.example, .dockerignore
```

---

## 3. Configuration

All settings come from environment variables (prefix `NFMUST_`) or `.env`.
See `.env.example` for the full list (repository names, ontology graph,
namespaces, SHACL toggle, auth, timeout).

### CORS (browser access from the frontend console)

Browser requests from a different origin (e.g. the Vite dev server on
`:5173` calling this API on `:8000`) are blocked by the browser unless the
API sends the right CORS headers — without that, every request fails at the
network layer before reaching any route, which looks identical to "API
unreachable" from the frontend's point of view. The allowed origins are
configurable:

```bash
NFMUST_CORS_ALLOWED_ORIGINS=http://localhost:5173,http://137.194.70.195:5173
```

Defaults already cover the Vite dev server (`:5173`) and preview (`:4173`)
on `localhost`/`127.0.0.1`. Add the frontend's deployed origin here whenever
it moves off localhost.

---

## 4. Quickstart

### Option A — Local Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                       # adjust GraphDB URL if needed
python scripts/bootstrap.py                # create repos + load ontology
uvicorn app.main:app --reload --port 8000
python scripts/ingest_examples.py          # load the sample documents in order
```

### Option B — Production (API only, external GraphDB)

For a GraphDB that already runs elsewhere :

```bash
cp .env.example .env        # set NFMUST_GRAPHDB_BASE_URL and any credentials
docker compose -f docker-compose.yml up -d --build
```

The production image:
* is **multi-stage** (deps built in an isolated venv, copied into a slim
  runtime) and runs as a **non-root** user;
* is served by **gunicorn** with uvicorn workers (`WEB_CONCURRENCY` workers,
  default 4) — a process manager that restarts crashed workers;
* declares a container **HEALTHCHECK** on `GET /health` (liveness);
* runs `scripts/bootstrap.py` once at startup via its entrypoint (disable with
  `NFMUST_BOOTSTRAP=false` — do this on extra replicas so they don't race to
  create the repository).

Health/readiness endpoints:
* `GET /health` — liveness, always 200 while the process is up.
* `GET /ready` — readiness, 200 when the GraphDB repository answers, 503 when
  it doesn't (suitable for a Kubernetes readiness probe or a load balancer).


---

## 5. Endpoints

### Per-entity routes (explicit)

Each of the 7 TMF entity types has its own explicit, literal-path CRUD
surface — not resolved dynamically from a `kind` parameter. POST/PUT enforce
that the posted document's detected TMF type matches the route (rejected with
`entity_type_mismatch` otherwise):

| Entity                   | List / Create              | Read / Replace / Delete one          |
|---------------------------|-----------------------------|----------------------------------------|
| Service Catalog            | `GET` `POST /service-catalog`        | `GET` `PUT` `DELETE /service-catalog/{id}`        |
| Service Category           | `GET` `POST /service-category`       | `GET` `PUT` `DELETE /service-category/{id}`       |
| Service Candidate          | `GET` `POST /service-candidate`      | `GET` `PUT` `DELETE /service-candidate/{id}`      |
| Generic CFSS               | `GET` `POST /generic-cfs`            | `GET` `PUT` `DELETE /generic-cfs/{id}`            |
| Concrete CFSS              | `GET` `POST /concret-cfs`            | `GET` `PUT` `DELETE /concret-cfs/{id}`            |
| RFSS                       | `GET` `POST /concret-rfs`            | `GET` `PUT` `DELETE /concret-rfs/{id}`            |
| Service Management Profile | `GET` `POST /service-profile`        | `GET` `PUT` `DELETE /service-profile/{id}`        |

Each `POST`/`PUT` carries a schema-conformant example in Swagger, sourced from
`examples/schemas/*.schema.json` (the templates supplied for this API). 

### Catalog discovery (Concrete CFSS only)

| Method & path                | Purpose                                                        |
|-------------------------------|----------------------------------------------------------------|
| `GET  /concret-cfs/filter`     | **Typical filtered query**: filter by `catalog`, `category`, `characteristic`, `value` (local, no federation) |
| `POST /concret-cfs/search`    | **Multi-criteria search**: combine several characteristics, each tagged `local` or `generic` |

### Cross-cutting

| Method & path                | Purpose                                                        |
|-------------------------------|----------------------------------------------------------------|
| `POST /ingest`                | Ingest any TMF document (auto-detect type dynamically, validate, store) — kept for bulk/scripted ingestion where the caller doesn't want to pick a per-entity route |
| `POST /validate`              | Dry-run: SHACL + reference checks, **no write**                |
| `GET  /ontology`               | Fetch the ontology as JSON-LD                                  |
| `POST /ontology/reload`       | Reload the bundled ontology into all repositories               |
| `PUT  /ontology`               | Replace the ontology (Turtle body) in all repositories          |
| `POST /query`                 | Run a free SPARQL query                  |

### Health / readiness

`GET /health` (liveness, no GraphDB call) and `GET /ready` (readiness, checks
GraphDB connectivity) back the container HEALTHCHECK and any orchestrator
probes.


## 6. Query examples

**Local** — concrete CFSS with `Availability = High`
in category *Slice_5G* under catalog *NF_MUST_Catalog*:

```
GET /concret-cfs/filter?catalog=NF_MUST_Catalog&category=Slice_5G&characteristic=Availability&value=High
```

**Multi-criteria, local scope** — in practice, per TMF633, the committed
operational values of a specific commercial offer (the actual guaranteed
throughput, the actual chosen isolation level, the actual coverage area, a
possibly-overridden availability) are recorded on the **concrete** spec
itself. Find a concrete CFSS with `Availability = High`, a guaranteed downlink
throughput of `5000000`, coverage in `FR` and `Isolation = 2` — all four
checked directly on the concrete spec, no federation:

```
POST /concret-cfs/search
{
  "criteria": [
    {"characteristic": "Availability", "value": "High", "scope": "local"},
    {"characteristic": "Downlink throughput per network slice: Guaranteed downlink throughput quota", "value": "5000000", "scope": "local"},
    {"characteristic": "Area of Service", "value": "FR", "scope": "local"},
    {"characteristic": "Isolation level: Isolation", "value": "2", "scope": "local"}
  ]
}
```

**Multi-criteria, generic scope** — the generic parent typically only
declares the *admissible range or set of options* (e.g. an availability range
or an enumerated isolation level "0 | 1 | 2"), not a committed value. Use
`scope: "generic"` to check that kind of range/option membership, or when a
characteristic genuinely has no concrete-side override:

```
POST /concret-cfs/search
{
  "criteria": [
    {"characteristic": "Availability", "value": "Very high", "scope": "generic"}
  ]
}
```

`scope: "local"` criteria are matched directly against the ConcreteCFSS
(cheap, no extra join); `scope: "generic"` criteria are all matched against
its GenericCFSS parent in the same query — an ordinary join across named
graphs in the same repository. `catalog` / `category` /
`limit` can be added at the top level of the body to narrow further.

---

## 7. Tests

```bash
pytest -q
```

Covers type detection, URI matching, the `exposes`→ConcreteCFSS
fix, SHACL conformance (and rejection of a candidate without `exposes`), and the
full ingestion pipeline against an in-memory fake GraphDB (routing, reference
ordering, create-vs-replace).

---


