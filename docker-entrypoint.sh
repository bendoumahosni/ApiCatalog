#!/bin/sh
# ---------------------------------------------------------------------------
# Container entrypoint for the NF-MUST Catalog API.
#
# If NFMUST_BOOTSTRAP=true (default), run scripts/bootstrap.py first to create
# the GraphDB repository (if missing) and load the ontology, then exec the
# given command (gunicorn by default — see Dockerfile CMD).
#
# Set NFMUST_BOOTSTRAP=false to skip bootstrap, e.g. when running multiple API
# replicas: bootstrap once (a one-shot job or the first replica) and start the
# rest with bootstrap disabled so they do not race to create the repository.
#
# `exec "$@"` replaces the shell with the server process so it becomes PID 1
# and receives stop signals directly (clean shutdown).
# ---------------------------------------------------------------------------
set -e

if [ "${NFMUST_BOOTSTRAP:-true}" = "true" ]; then
    echo "[entrypoint] Running bootstrap (set NFMUST_BOOTSTRAP=false to skip)..."
    python scripts/bootstrap.py
else
    echo "[entrypoint] NFMUST_BOOTSTRAP=false — skipping bootstrap."
fi

echo "[entrypoint] Starting: $*"
exec "$@"
