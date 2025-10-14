#!/usr/bin/env python3
"""
Purpose:
  Parse the project's docker-compose.yml and compute dependency topology limited
  to "node" services (label denaro.node=true). Emit a single JSON file used by
  per-service healthchecks to decide whether readiness probing is needed.

Hardcoded paths:
  COMPOSE_FILE = "/project/docker-compose.yml"         (bind-mounted by the 'topology' service)
  OUTPUT_FILE  = "/shared/node-topology/topology.json" (shared volume mounted read-only into nodes)

Output (topology.json):
  {
    "nodes": ["node-8002", "node-8003", ...],              # node services only
    "dependents": {                                        # reverse edges among node services
      "node-8005": ["node-8002"],
      "node-8002": ["node-8003"],
      ...
    },
    "generated_at": "<RFC3339 timestamp>"
  }

- Only node->node dependency edges are recorded.
- A node with no dependents may be absent from 'dependents' or mapped to [].
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Set
import yaml  # PyYAML

COMPOSE_FILE = "/project/docker-compose.yml"
OUTPUT_FILE = "/shared/node-topology/topology.json"


def atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "wb") as w:
            w.write(data)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def normalize_labels(spec: Dict[str, Any]) -> List[str]:
    labels = spec.get("labels", [])
    if isinstance(labels, dict):
        return [f"{k}={v}" for k, v in labels.items()]
    return [str(x) for x in labels]


def is_node_service(spec: Dict[str, Any]) -> bool:
    labels = normalize_labels(spec)
    for entry in labels:
        if entry == "denaro.node=true":
            return True
        if entry.startswith("denaro.node=") and entry.split("=", 1)[1].lower() == "true":
            return True
    return False


def get_upstreams(spec: Dict[str, Any]) -> List[str]:
    dependencies = spec.get("depends_on")
    if dependencies is None:
        return []
    if isinstance(dependencies, list):
        return [x for x in dependencies if isinstance(x, str)]
    if isinstance(dependencies, dict):
        return list(dependencies.keys())
    return []


def main() -> int:
    with open(COMPOSE_FILE, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    services: Dict[str, Dict[str, Any]] = doc.get("services") or {}

    node_names: Set[str] = {name for name, spec in services.items() if is_node_service(spec)}

    # Build reverse mapping: upstream_node -> [dependent_nodes...]
    dependents: Dict[str, List[str]] = {name: [] for name in node_names}
    for dependency_name, dependency_spec in services.items():
        if dependency_name not in node_names:
            continue  # only node services can be dependents in this graph
        for up in get_upstreams(dependency_spec):
            if up in node_names:
                dependents.setdefault(up, []).append(dependency_name)

    topology = {
        "nodes": sorted(node_names),
        "dependents": {k: sorted(v) for k, v in dependents.items() if v},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    data = json.dumps(topology, indent=2).encode("utf-8")
    atomic_write(OUTPUT_FILE, data)
    print(f"topology: wrote {OUTPUT_FILE} for {len(node_names)} nodes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
