"""Gunicorn configuration for the NF-MUST Catalog API (production).

All values can be overridden by environment variables so the same image runs
unchanged in dev, staging and prod. Worker count defaults to WEB_CONCURRENCY
(set in the Dockerfile); tune it to roughly (2 x CPU cores) + 1 for a
CPU-bound workload, or lower for small nodes.
"""

from __future__ import annotations

import os

# Network
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")

# Workers — uvicorn workers are set on the command line (--worker-class).
workers = int(os.getenv("WEB_CONCURRENCY", "4"))

# Timeouts (seconds). Keep timeout >= the API's own HTTP timeout to GraphDB
# (NFMUST_HTTP_TIMEOUT) so a slow GraphDB call is not killed mid-flight by the
# worker timeout.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# Recycle workers periodically to bound the effect of any slow leak.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# Logging to stdout/stderr (collected by the container runtime).
accesslog = os.getenv("GUNICORN_ACCESSLOG", "-")
errorlog = os.getenv("GUNICORN_ERRORLOG", "-")
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
