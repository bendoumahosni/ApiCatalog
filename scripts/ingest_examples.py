#!/usr/bin/env python3
"""Ingest the bundled example documents through the running API.

The order respects the reference constraints:
    RFSS -> generic CFSS -> concrete CFSS -> catalog/category/candidate

(The repository ships RFSS-less examples for the two NSST RFSS referenced by the
eMBB specs; if you have their JSON, add them at the front of ORDER.)

Usage:
    python scripts/ingest_examples.py [API_BASE_URL]
    (default API_BASE_URL = http://localhost:8000)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

ORDER = [
    "eMBB_Generic_CFSS.json",   # generic (depends on RFSS — seed RFSS first if enforced)
    "Concret_CFSS.json",        # concrete (specializes generic, dependsOn RFSS)
    "service_catalog.json",     # catalog/category/candidate unit
    "service_category.json",
    "service_candidate.json",
]


def main() -> int:
    api = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    with httpx.Client(timeout=60) as client:
        for name in ORDER:
            payload = json.loads((EXAMPLES / name).read_text())
            resp = client.post(f"{api}/ingest", json=payload)
            tag = "OK " if resp.status_code < 300 else "ERR"
            print(f"[{tag}] {name:28s} -> {resp.status_code} {resp.text[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
