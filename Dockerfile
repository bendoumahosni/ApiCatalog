# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Production image for the NF-MUST Catalog API.
#
# Multi-stage:
#   * builder  — installs dependencies into an isolated virtualenv
#   * runtime  — slim, non-root, carries only the venv + app code
#
# Served by gunicorn with uvicorn workers (a process manager that restarts
# crashed workers), behind the container's own HEALTHCHECK hitting /health.
# ---------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build the venv in /opt/venv so it can be copied wholesale into the runtime
# stage without dragging in build tooling.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt


# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # gunicorn settings (override at run time if needed)
    WEB_CONCURRENCY=4 \
    GUNICORN_TIMEOUT=60

# curl is used by the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    # Non-root runtime user.
    && groupadd --system app \
    && useradd --system --gid app --no-create-home --home /app app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app
COPY ontology ./ontology
COPY shapes ./shapes
COPY scripts ./scripts
COPY examples ./examples
COPY gunicorn_conf.py ./gunicorn_conf.py
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER app

EXPOSE 8000

# Liveness check — /health does not touch GraphDB, so a GraphDB blip will not
# get the API container killed (use /ready for dependency readiness).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Entrypoint runs the optional bootstrap, then exec's the CMD below.
ENTRYPOINT ["docker-entrypoint.sh"]

# uvicorn workers managed by gunicorn. WEB_CONCURRENCY controls worker count.
CMD ["gunicorn", "app.main:app", \
     "--config", "gunicorn_conf.py", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000"]
