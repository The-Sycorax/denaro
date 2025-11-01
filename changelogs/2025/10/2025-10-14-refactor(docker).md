**refactor(docker): implement topology-aware healthcheck for Docker setup**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [fa34e7bf5e27d8220c4100e399c60fbc47b9b4ce](https://github.com/The-Sycorax/denaro/commit/fa34e7bf5e27d8220c4100e399c60fbc47b9b4ce)

**Date**: 2025-10-14

---

## Overview:

This refactor overhauls the Docker healthcheck mechanism to improve runtime efficiency and reduce redundant operations in multi-node setups. It introduces dynamic service topology detection, and replaces basic Docker healthchecks with a new topology-aware probing system. 

The previous implementation was quite insufficient and executed continuous `wget` requests for every node, regardless of whether downstream services depended on it. This resulted in unnecessary processing and polluted node logs with endless self-requests to the `/get_status` endpoint, although not a major issue, this refactor eliminates that.

This refactor also cleans up the project's structure by moving all Docker-related files into a single `docker/` directory. This allows for a clear separation between the actual container setup and application code. This reorganization required updating several file paths throughout the configuration. Additionally, shared volumes paths now use a single `/shared` directory, and the entrypoint script has been updated to use absolute paths to prevent potential execution issues.

---

## Modified Files:
- ### `Dockerfile`
    - Docker-related files are now sourced from the `docker/` subdirectory within build context and copied into `/app/docker/` inside of the image.
    
    - All `COPY`, `RUN`, `HEALTHCHECK`, and `ENTRYPOINT` directives have been updated to reflect paths under the new `docker/` directory structure.
    
    - Both `docker-entrypoint.sh` and `docker-healthcheck.py` are now made executable in one layer via a single `chmod` command.

    - `HEALTHCHECK` instruction changed from using the `wget` command to the new `docker-healthcheck.py` Python script.
    
    - Existing healthcheck timing parameters have not been changed.

---

- ### `docker-compose.yml`
    -   A new `topology` service has been introduced to dynamically generate a node dependency map upon startup.
        -   It utilizes the `python:3.12-alpine` image, installing `PyYAML` and executes the new `generate-topology.py` script.
        -   The service bind-mounts the `docker-compose.yml` file as a read-only input for the script.
    
    -   A corresponding `node-topology` shared volume has been created to store the dependency map, making it available to all other nodes.
    
    -   All node services have been updated to integrate with this new system:
        -   A `depends_on` condition now requires the `topology` service to complete successfully before any node can start.
        -   The `node-topology` volume is mounted read-only at `/shared/node-topology` in each node, providing access to the dependency map.
    
    -   Several build and configuration paths have been updated for consistency and to support the new structure:
        -   Node services are now built from the parent directory (`context: ".."`) with the `Dockerfile` explicitly specified as `./docker/Dockerfile`.
        -   Consequently, environment file paths have been updated from `.env` to `../.env`  to account for context change.
        -   The internal mount path for the existing `node-registry` volume has been changed from `/registry` to `/shared/node-registry`.

---

- ### `docker-entrypoint.sh`
    - `REGISTRY_DIR`, and  `REGISTRY_FILE` have been updated to match new volume mount structure.
    - File paths for `.env` generation and application execution changed to absolute paths (`/app/.env`, `/app/run_node.py`).

---

## Added Files:
- ### `generate-topology.py`:
    - ### Overview:
        - Topology generation is handled by this script. It runs once at startup within the topology service to analyze the `docker-compose.yml` file and build a dependency map.
            - This map is then consumed by the healthcheck script in each node to determine if it has downstream dependents.
            - The script identifies nodes by filtering for the `denaro.node=true` label and parses their `depends_on` sections to build the topology.
        - The output results in a "reverse dependency map" where each key is an upstream node and its value is a list of nodes that depend on it.
        - This map is subsequently written to the shared volume at `/shared/node-topology/topology.json`, making it available to all other nodes.

        ---

    - ### Execution Flow:
        - **1․** The script begins by loading and parsing the YAML content from `/project/docker-compose.yml`.

        - **2․** It then filters the services to identify nodes, looking for a `denaro.node=true` label. This process correctly handles labels defined as either lists or dictionaries.

        - **3․** For each identified node, it extracts its upstream dependencies from the `depends_on` section, supporting both list and dictionary formats.

        - **4․** A reverse dependency map is built, where each key represents an upstream node and its value is a list of downstream nodes that depend on it. Only node-to-node relationships are included.

        - **5․** The script constructs the final JSON output, which includes a sorted list of all nodes, the dependency map (excluding nodes with no dependents), and an RFC3339 timestamp.

        - **6․** Finally, the resulting JSON is written to `/shared/node-topology/topology.json` using an atomic write pattern (writing to a temporary file then renaming it) to prevent other services from reading an incomplete file.
    
        ---

    - ### Output Eample:
        ```json
        {
          "nodes": ["node-3006", "node-3007", "node-3008"],
          "dependents": {
            "node-3006": ["node-3007", "node-3008"],
            "node-3007": ["node-3008"]
          },
          "generated_at": "2025-10-14T12:34:56.789012+00:00"
        }
        ```
        - `node-3006` has 2 dependents -> Healthcheck will probe
        - `node-3007` has 1 dependent -> Healthcheck will probe
        - `node-3008` has 0 dependents -> Healthcheck will skip
    
        ---

    - ### Constants:
        - **`COMPOSE_FILE` (Hardcoded):** Docker Compose file path (`/project/docker-compose.yml`).
        - **`OUTPUT_FILE` (Hardcoded):** Output file path (`/shared/node-topology/topology.json`).
---

- ### `docker-healthcheck.py`:
    - ### Overview:
        - Docker healthchecks are now handled by this script. It uses the generated dependency map to determine if the current node has any downstream dependents.
            - If a node has no downstream dependents, the healthcheck passes immediately.
            
            - If a node has downstream dependents, the script will perform an HTTP request to the node's `/get_status` endoint. If the response contains `{"ok": true}`, it is a signal that the current node is operational and the healthcheck passes.
        
        - Docker healthchecks continuously run at a set interval and there is no way to pause or disable it on a running container. 
            - The script therefore, has been designed to ensure that after the first successful healthcheck, a readiness file is created at `/tmp/node_ready`. This file serves as a flag to mark the current node as healthy.
            
            - All subsequent healthchecks will pass immediately if the readiness file exists, bypassing redundant processing and HTTP requests.

    - *The healthcheck allows Docker Compose to enforce a reliable startup order via the `service_healthy` condition, ensuring dependent services do not start until the upstream application is ready. The reason for this is beacause, by default Docker Compose only confirms that the container has started, but not that the application is ready.*
        
        ---

    - ### Execution flow:
        - **1․** The script first determines if the node has already been marked healthy by checking if the readiness file (`/tmp/node_ready`) exists. 
            - If the readiness file exists then the node is deemed healthy, the script exits successfully.
            - If the readiness file dose not exist, the script loads the topology JSON data from the shared `node-topology` volume (`/shared/node-topology/topology.json`).
        
        - **2․** To determine if the current node has any downstream dependants, the script queries the current node in the `dependents` map of the topology data. *The `NODE_NAME` environment variable is used to identify the current node within the map.*

        - **3․**  If the current node has no dependents, it is deemed healthy. 
            - The script then creates the readiness file to mark the node as healthy and exits successfully.

        - **4.** If the node does have dependents, an HTTP request is sent to its `/get_status` API endpoint (`http://127.0.0.1:{node_port}/get_status`).
            - The script then parses the response body as JSON and verifies if it contains `{"ok": true}`. If so, it indicates that the current node is operational.
                
        - **5․**  Upon successful validation, the script creates the readiness file to mark the node as healthy and exits successfully. 
    
        ---

    - ### Exit Codes:
        - **`0`:** Healthy
        - **`1`:** Unhealthy

    - ***If a node is deemed healthy, the script will exit with a code of `0` and the Docker healthcheck will pass.***
    - ***If a node is deemed unhealthy or if the script fails at any point, it will exit with a code of `1` and the Docker healthcheck will fail.***

        ---

    - ### Constants:
        - **`NODE_NAME`:** The name of the Denaro node.
        - **`DENARO_NODE_PORT`:** The port number that the Denaro node is running on.
        - **`TOPOLOGY_FILE`:** Path to the topology file (`/shared/node-topology/topology.json`).
        - **`HEALTHCHECK_READINESS_FILE`:** Readiness file path (`/tmp/node_ready`).