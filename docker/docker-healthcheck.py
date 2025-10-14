#!/usr/bin/env python3
"""
Name: docker-healthcheck.py

Purpose
  One-time conditional healthcheck for Denaro nodes. Uses `topology.json` to decide
  whether to probe. If probing is required, it calls `/get_status` endpoint exactly
  once and only marks ready if the response JSON has {"ok": true}. After first
  success, a per-boot readiness file prevents further probes.

Inputs (env)
  NODE_NAME                    [required] service key for this node (e.g., "node-8005")
  DENARO_NODE_PORT             [required] container-internal port (e.g., "8005")
  TOPOLOGY_FILE                [default "/shared/node-topology/topology.json"]
  HEALTHCHECK_READINESS_FILE       [default "/tmp/node_ready"]

Exit codes
  0 = healthy (or skipped); 1 = unhealthy/error.
"""
import json
import os
import sys
from typing import Any, Dict

import requests


def _log(msg: str) -> None:
    sys.stderr.write(f"[healthcheck] {msg}\n")


def _load_topology(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _has_node_dependents(topo: Dict[str, Any], name: str) -> bool:
    dependency_map = topo.get("dependents") or {}
    return bool(dependency_map.get(name) or [])


def _http_probe(url: str) -> bool:
    """
    True if HTTP succeeds and body JSON object contains {"ok": true}.
    """
    # Use a single float timeout (both connect and read)
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    try:
        obj = r.json()
    except Exception as e:
        _log(f"json decode error: {e}")
        return False
    return isinstance(obj, dict) and obj.get("ok") is True


def _touch_once(path: str) -> None:
    flags = os.O_CREAT | os.O_WRONLY | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as w:
        pass


def main() -> int:
    node_name = os.getenv("NODE_NAME")
    node_port = os.getenv("DENARO_NODE_PORT")
    url = f"http://127.0.0.1:{node_port}/get_status" if node_port else None
    
    topology_path = os.getenv("TOPOLOGY_FILE", "/shared/node-topology/topology.json")
    readiness_path = os.getenv("HEALTHCHECK_READINESS_FILE", "/tmp/node_ready")

    # Already marked ready
    if os.path.exists(readiness_path):
        return 0

    # Required env
    if not node_name:
        _log("NODE_NAME not set"); return 1
    if not node_port:
        _log("DENARO_NODE_PORT not set"); return 1
    if not url:
        _log("HEALTHCHECK_URL could not be constructed"); return 1

    # Skip probing if unused, then mark ready
    try:
        topology = _load_topology(topology_path)
        if not _has_node_dependents(topology, node_name):
            _touch_once(readiness_path)
            return 0
    except FileNotFoundError:
        _log(f"{topology_path} missing -> performing probe")
    except Exception as e:
        _log(f"failed to parse {topology_path}: {e} -> performing probe")

    # One-time probe; mark ready only when ok=true
    try:
        if _http_probe(url):
            _touch_once(readiness_path)
            _log("probe ok=true; Node is ready.")
            return 0
        _log("probe failed")
        return 1
    except requests.RequestException as e:
        _log(f"probe HTTP error: {e}")
        return 1
    except Exception as e:
        _log(f"probe error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
