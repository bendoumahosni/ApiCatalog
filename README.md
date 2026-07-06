# NF-MUST Catalog API

A FastAPI backend that populates a TMF service-core ontology with **instances**
and stores them in **GraphDB**. The flow is exactly the one we converged on:

```
JSON (TMF633)  →  validate (SHACL + reference checks)  →  RDF/Turtle  →  named graph in GraphDB
```

It also exposes the catalog **discovery queries** and **ontology (TBox)
management**.

---

## 1. Design in one screen

**One repository, one named graph per instance.** Every TMF entity type
(catalog, category, candidate, generic CFSS, concrete CFSS, RFSS, management
profile) is stored in the **same** GraphDB repository (`app/config.py`,
`Settings.repository`). What used to be four separate repositories is now one
— the routing table still exists (`Settings.entity_to_repository`) but every
entry now resolves to the same place:

| Document type (`@type`)                 | Repository (single)   |
|-----------------------------------------|------------------------|
| ServiceCatalog                          | `nfmust_catalog`       |
| ServiceCategory                         | `nfmust_catalog`       |
| ServiceCandidate                        | `nfmust_catalog`       |
| ConcreteCFSS (CFSS + `specializes`)     | `nfmust_catalog`       |
| GenericCFSS (CFSS, no `specializes`)    | `nfmust_catalog`       |
| RFSS (ResourceFacing…)                  | `nfmust_catalog`       |
| ServiceManagementProfile                | `nfmust_catalog`       |

Every top-level entity is still written into its own **named graph**, named
after its own URI (the same `entity_uri == named_graph` convention as
before). This is what makes CRUD surgical: `PUT` = drop + reload that one
graph; `DELETE` = `DROP GRAPH <entity-uri>` — both leave every other entity
untouched, exactly as when there were four repositories. The ontology lives
in its own named graph (`http://vocab.nfmust.ie/tmf-service-core`), kept
separate from instance data so reasoning applies uniformly and an ontology
update is a single clean graph replace.

Because everything is now one repository, a query that joins across entity
types — e.g. a ConcreteCFSS to its GenericCFSS parent, or a candidate to the
concrete spec it exposes — is an **ordinary SPARQL join across named graphs**.
The federated `SERVICE` queries the four-repository layout needed are gone;
see §6.

**Two complementary validations before any write.**
* **SHACL** (`shapes/shapes.ttl`) checks structure and business rules per entity
  type — e.g. *a candidate exposes exactly one ConcreteCFSS*, *a GenericCFSS
  specializes nothing*, *if a ConcreteCFSS specializes something it must be a
  GenericCFSS*. (A ConcreteCFSS is **not** required to specialize a
  GenericCFSS — bulk imports often have none.) OWL alone cannot reject such
  data (open-world), which is why SHACL is used.
* **Strong reference checks** (ASK queries) can verify that referenced entities
  already exist, routed by `@referredType`
  (`ResourceFacingServiceSpecification` → RFSS,
  `CustomerFacingServiceSpecification` → generic/concrete CFSS). **These checks
  are disabled by default** (`NFMUST_REFERENCE_CHECKS_ENABLED=false`) so that
  bulk/automated ingestion need not control arrival order; enable them to
  enforce the order `RFSS → generic CFSS → concrete CFSS →
  catalog/category/candidate`.

When reference checks are enabled, intra-unit links inside the
catalog/category/candidate group are still **not** pre-validated (they form one
unit and would otherwise be circular).

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
├── docker-compose.yml          # dev: API + GraphDB
├── docker-compose.api-only.yml # API only, external GraphDB
├── docker-compose.prod.yml     # production: API only, .env-driven
├── Makefile, requirements.txt, .env.example, .dockerignore
```

---

## 3. Configuration

All settings come from environment variables (prefix `NFMUST_`) or `.env`.
Switching environments is one line:

```bash
# development
NFMUST_GRAPHDB_BASE_URL=http://localhost:7200
# production
NFMUST_GRAPHDB_BASE_URL=http://137.194.70.195:7200
```

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

### Option A — Docker (API + GraphDB together, development)

```bash
docker compose up --build
```

This starts GraphDB on `:7200`, then the API container's entrypoint creates the
repository, loads the ontology, and starts the server on `:8000`. Open
http://localhost:8000/docs.

### Option B — Local Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                       # adjust GraphDB URL if needed
python scripts/bootstrap.py                # create repos + load ontology
uvicorn app.main:app --reload --port 8000
python scripts/ingest_examples.py          # load the sample documents in order
```

### Option C — Production (API only, external GraphDB)

For a GraphDB that already runs elsewhere (a Kubernetes cluster, a dedicated
server, or exposed on the Docker host at `localhost:7200`):

```bash
cp .env.example .env        # set NFMUST_GRAPHDB_BASE_URL and any credentials
docker compose -f docker-compose.prod.yml up -d --build
```

The production image:
* is **multi-stage** (deps built in an isolated venv, copied into a slim
  runtime) and runs as a **non-root** user;
* is served by **gunicorn** with uvicorn workers (`WEB_CONCURRENCY` workers,
  default 4) — a process manager that restarts crashed workers;
* declares a container **HEALTHCHECK** on `GET /health` (liveness; does not
  touch GraphDB, so a GraphDB blip will not kill the API);
* runs `scripts/bootstrap.py` once at startup via its entrypoint (disable with
  `NFMUST_BOOTSTRAP=false` — do this on extra replicas so they don't race to
  create the repository).

Health/readiness endpoints:
* `GET /health` — liveness, always 200 while the process is up.
* `GET /ready` — readiness, 200 when the GraphDB repository answers, 503 when
  it doesn't (suitable for a Kubernetes readiness probe or a load balancer).

Tuning is via environment variables (no rebuild): `WEB_CONCURRENCY`,
`GUNICORN_TIMEOUT`, `NFMUST_GRAPHDB_BASE_URL`, `NFMUST_GRAPHDB_USER` /
`NFMUST_GRAPHDB_PASSWORD`, `NFMUST_CORS_ALLOWED_ORIGINS`, etc. — see
`.env.example`.

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
`examples/schemas/*.schema.json` (the templates supplied for this API). For
**Generic CFSS, Concrete CFSS, RFSS, and Service Management Profile**, the
entity's URI is minted from `id` alone (no type prefix) — these 4 examples all
use the literal placeholder id `"String"` to show the expected shape, so
posting more than one of them as-is **will collide**; replace the placeholder
with a real, unique id before posting. The route's Swagger description
repeats this warning. Catalog/Category/Candidate don't have this issue (their
URI is prefixed by entity type).

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
| `POST /query`                 | Run a free SPARQL query against one repository                  |

### Health / readiness

`GET /health` (liveness, no GraphDB call) and `GET /ready` (readiness, checks
GraphDB connectivity) back the container HEALTHCHECK and any orchestrator
probes — see §4 Option C.

### Removed

`GET /` (API metadata) and `GET /concret-cfs/search` as a *GET* (the
single-criterion convenience endpoint) have been removed. Combined search now
lives exclusively at `POST /concret-cfs/search`.

### Note: no generic `/{kind}` catch-all

There is **no** dynamic `/{kind}` router. Every entity type is served by its
own explicit per-entity router (`app/routers/entities_explicit/`, one file per
entity), all built from the same factory so they share one `IngestionService`
pipeline. `app/routers/entities.py` retains only the shared
`SearchCriterion`/`SearchRequest` request models used by the concrete-CFSS
search endpoint; its former duplicate `/{kind}` CRUD router was removed because
it was permanently shadowed by the explicit routers and never reachable.
Entity segments are: `concret-cfs`, `generic-cfs`, `concret-rfs`,
`service-profile`, `service-catalog`, `service-category`, `service-candidate`.

---

## 6. Query examples

**Local** — concrete CFSS with `Availability = High`
in category *Slice_5G* under catalog *NF_MUST_Catalog*:

```
GET /concret-cfs/filter?catalog=NF_MUST_Catalog&category=Slice_5G&characteristic=Availability&value=High
```

**Joined to the generic parent** — concrete CFSS whose generic parent permits
`Availability = Very high`. Under the single-repository layout this is an
ordinary SPARQL join across named graphs, no federation involved:

```
GET /concret-cfs/search?generic_characteristic=Availability&generic_value=Very%20high
```

generates:

```sparql
PREFIX tmf: <http://vocab.nfmust.ie/tmf-service-core#>
SELECT DISTINCT ?concret ?name WHERE {
  ?concret a tmf:ConcreteCFSS ; tmf:name ?name ; tmf:specializes ?generic .
  ?generic tmf:hasCharacteristic [
    tmf:name "Availability" ;
    tmf:hasCharacteristicValue [ tmf:value "Very high" ]
  ] .
}
```

Note the `(name, value)` pair is written as a single connected **blank-node**
block, not as two separately-named variables joined by sharing a name. This
matters even within a single repository: when a query mixes a "local"
characteristic block and a "generic" one in the same query, an exact-match
join on the wrong shared variable could decouple a "name" constraint from the
"value" constraint that was meant to apply to the very same characteristic
node — letting an unrelated characteristic elsewhere in the repository "leak"
through if it happens to carry the same literal value. Blank nodes make that
decoupling syntactically impossible.

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
graphs in the same repository, not a federated call. `catalog` / `category` /
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

## 8. Notes / known follow-ups

* The two NSST RFSS referenced by the eMBB specs are not shipped as examples; if
  reference checking is on, ingest their JSON first (or post
  them ahead of the generic in `scripts/ingest_examples.py`).
* `ServiceManagementProfile` ingestion is routed and stored, but its SHACL shape
  is intentionally minimal — extend `shapes/shapes.ttl` once the profile JSON
  schema is fixed.
* GraphDB repository-creation in `scripts/bootstrap.py` uses the Workbench REST
  API; if your GraphDB version differs, create the repository from the UI with
  `scripts/repo-config.ttl.template` and re-run (ontology loading uses the
  standard RDF4J protocol and is version-stable).
* `GET /health` and `GET /ready` are available again (they back the container
  HEALTHCHECK and deployment probes). `/health` is a pure liveness check that
  never touches GraphDB; `/ready` reports GraphDB reachability and returns 503
  when it is down. Any frontend that wants to display reachability can poll
  `/ready`.
```
