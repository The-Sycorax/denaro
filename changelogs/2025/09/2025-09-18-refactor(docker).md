**refactor(docker): refactor compose topology, update Dockerfile, and implement entrypoint orchestration**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [628fee1d4e96f78a7bfb03f0a15073164e3f17ae](https://github.com/The-Sycorax/denaro/commit/628fee1d4e96f78a7bfb03f0a15073164e3f17ae)

**Date**: 2025-09-18

---

## Overview:

**This refactor introduces a fundamental overhaul of Denaro's Docker environment setup. It is intended to improve automation, scalability, and configuration management by replacing the previous static setup with a dynamic, self-configuring startup process. This refactor now facilitates realistic multi-node deployments, and allows for testing scenarios with complex P2P network topologies.**

- The `docker-compose.yml` topology is refactored for maintainability. Common service configuration is consolidated with YAML anchors. Shared environment variables are supplied by the global `.env` file and by a shared environment map that services merge explicitly. Bind mounts are replaced with named volumes for persistent data, including a shared registry volume and per-service data volumes.

- The `Dockerfile` has been optimized by updating the base image to `python:3.11-slim`, consolidating system package installation into a single layer, and copying only nessessary files to reduce the build context size.

- A new `docker-entrypoint.sh` script has been introduced to manage container lifecycle. The script orchestrates the container startup sequence, handles automated bootstrap-node discovery via a shared peer registry, generates the application `.env`, and provisions per-node databases.

- To facilitate public node behavior over the internet, the entrypoint script also includes optional support for exposing a node on the Internet by establishing an SSH reverse tunnel via [Pinggy.io's free tunnleing service](https://www.pinggy.io).

---

## Modified Files:

- #### **`Dockerfile`**:
  - The base image has been updated from `python:3.10.1-slim-buster` to `python:3.11-slim`.
  - Python environment variables have been set:
    - `PYTHONDONTWRITEBYTECODE=1`:  To avoid `.pyc` files.
    - `PYTHONUNBUFFERED=1`: To enable unbuffered output.

  - System package installation has been consolidated into a single layer.
    
  - New runtime and build dependencies have been added:
    - `postgresql-client`: For `psql` and `pg_isready`.
    - `openssh-client`: For the Pinggy SSH tunnel.
    - `wget`: For the healthcheck.
    - `libgmp-dev` and `libpq-dev`: For compiling Python packages.
    
  - `libgmp3-dev` library has been replaced by `libgmp-dev`.
  
  - Only required assets are now copied into the Docker image:
    - `requirements.txt`
    - `./denaro`
    - `docker-entrypoint.sh`
    - `run_node.py`
  
  - The `Dockerfile` does not copy the repository `.env` into the Docker image. In this Docker setup, the repository-level `.env` acts as a global source of shared environment variables consumed by Compose.

  - Python dependencies are now installed with `--no-cache-dir` to reduce image size.
  
  - `docker-entrypoint.sh` is marked as executable.
  
  - A `HEALTHCHECK` instruction has been implemented that probes `http://localhost:${DENARO_NODE_PORT}/get_status` via the `wget` command. 
    - This allows `docker-compose.yml` to enforce a reliable startup order by using the `service_healthy` condition, ensuring dependent services do not start until the upstream application is ready.
    - The reason for this is beacause, by default Docker Compose only confirms that the container has started, but not that the application is ready.
  
  - The previous inline `sed` patching of `run_node.py` has been removed.
  
  - `CMD ["python", "run_node.py"]` has been replaced with `ENTRYPOINT ["./docker-entrypoint.sh"]`.
  
  ---

- #### **`docker-compose.yml`:**
  - A shared service anchor `x-denaro-node-base` has been added to consolidate common node settings such as `env_file`, `volumes`, `labels`, and `networks`.
  
  - Shared environment variables are now defined via the repository-level global `.env` file using `env_file`.
  
  - A shared environment map has been centralized and merged via YAML merge keys. The map is not global. Its keys apply only to services that explicitly include` <<: *denaro-node-env`. 
    
  - Standardized defaults are now included in the shared map:
    - **`DENARO_DATABASE_HOST: postgres`**
    - **`DENARO_NODE_HOST: 0.0.0.0`**

  - **`DENARO_DATABASE_HOST`** value has been renamed to **`postgres`**.
    
  - Explicit `container_name` declarations have been removed in favor of Docker-generated names.

  - The Postgres service now uses `postgres:14` and a named data volume.
    
  - Each node now mounts its own `/app` volume and a shared `/registry` volume.

  - Service startup ordering is enforced with `depends_on: [postgres]`.
    
  - Per-node `environment` keys at the service level are defined and may override entries from the shared map and global `.env`. See [Configuration](#configuration) section for more deatails.
    
  - The schema bind mount to `/docker-entrypoint-initdb.d` has been removed. Schema import is now handled by the entrypoint script on first run.
  
  - Bind mounts have been replaced by named volumes, including a shared `node-registry` and per-service volumes such as `postgres_data` and `node_3006_data`.

  - The Adminer service has been removed.
    
  - The internal network name has been standardized to `denaro-net` and services are configured to restart automatically using `restart: unless-stopped`.
    
  - External port publishing has been removed and traffic remains on the internal network.
    
---
    
## Added Files:

#### `docker-entrypoint.sh`

Node startup inside the container is orchestrated by this entrypoint script. Runtime configuration is prepared, optional bootstrap discovery is performed using a shared peer registry, the node specific PostgreSQL database and schema are provisioned on first run, and the node process is launched.

To facilitate public node behavior over the internet, the entrypoint script also includes optional support for exposing a node on the Internet by establishing an SSH reverse tunnel via [Pinggy.io's free tunnleing service](https://www.pinggy.io).

The script is designed to proceed on recoverable issues by using conservative fallbacks when dependent actions fail. Unrecoverable errors such as database command failures still cause exit due to `set -e`.

---

### Execution flow:

1. #### Initialization:
   - The shared registry directory is created at `/registry`. The peer registry file is tracked at `/registry/public_nodes.txt`.
   
   - A PostgreSQL compatible database name `denaro_${SANITIZED_NODE_NAME}` is computed by replacing hyphens in `NODE_NAME` with underscores. 
   
      - This value is held in the shell as `DB_NAME` and is persisted into the generated `.env` as `DENARO_DATABASE_NAME`. It is not exported into the entrypoint process environment.
   
   - ~~`DENARO_SELF_URL` is set to `http://${NODE_NAME}:${DENARO_NODE_PORT}` as a safe baseline and fallback.~~
      - *This logic was later updated in commit [ecf57a8b](https://github.com/The-Sycorax/denaro/commit/ecf57a8bf1675b5788ce38519b1a020a3d90021c) to support external overrides and to prevent the script from overwriting the variable if it was already set. It now defaults to `http://${NODE_NAME}:${DENARO_NODE_PORT}` only when it's left unset.*
   
   - Bootstrap intent is normalized. If `DENARO_BOOTSTRAP_NODE` is not provided, it is set to `'self'` by default.


2. #### **Optional Public Node Tunnleing**:
   - When **`ENABLE_PINGGY_TUNNEL`** is set to **`'true'`**:
      - The script will establish an SSH reverse tunnel via Pinggy's free tunnleing service (`free.pinggy.io:443`) to expose the node on the Internet. This is intended for testing public node behavior over the Internet. 
      
      - SSH output is captured in `/tmp/pinggy.log`. The script scans this file for an assigned public URL for up to 30 seconds. If found, `DENARO_SELF_URL` is updated to that public URL and is appended to `/registry/public_nodes.txt` so that it can be discovered by other nodes within the same Docker environment. 
        - Only public endpoints are registered. Private nodes do not add internal URLs to the registry.
      
      - If no public URL is captured within the 30 second wait window, the script falls back to the internal URL, disables tunneling, and prints the tail of `/tmp/pinggy.log` for diagnostics.

   - *Note: Pinggy's free tunnleing service limits sessions to 60 minutes, after which the tunnel will be disconnected. There is no mechanism to re establish the tunnel after it drops.*

3. #### Bootstrap node discovery and selection:
   - If `DENARO_BOOTSTRAP_NODE` is `discover`:
     - The script waits for entries to appear in `/registry/public_nodes.txt`. If the current node is public, it waits until at least two public URLs are present so that it can choose a peer that is not itself. If this current node is private, it waits for any public node to appear.
     
     - The wait is bounded to about `120 seconds`. If the expected count is not reached within this period, or if the only available URL is the node's own, the script falls back to using `DENARO_SELF_URL` as the bootstrap node.
     
     - When the registry contains enough entries, the script selects the first URL that is different from `DENARO_SELF_URL`. If no different URL is found, it falls back to `'self'`.
   
   - If `DENARO_BOOTSTRAP_NODE` is `self`, the script resolves it to the current `DENARO_SELF_URL`.
   
   - If `DENARO_BOOTSTRAP_NODE` is set to a fixed address, discovery and selection are skipped.

4. #### Application `.env` generation:
    - Each node's entrypoint generates its own `.env` file inside the container at startup using per service variables, derived values, and shared inputs from either the global `.env` or the shared environment map.
   - The following variables are written to `.env`:
     - `DENARO_SELF_URL`
     - `DENARO_BOOTSTRAP_NODE`
     - `DENARO_DATABASE_NAME`
     - `POSTGRES_USER`
     - `POSTGRES_PASSWORD`
     - `DENARO_DATABASE_HOST`
     - `DENARO_NODE_HOST`
     - `DENARO_NODE_PORT`
   
   - `NODE_NAME` and `ENABLE_PINGGY_TUNNEL` are not persisted to `.env`.
   
   - The generated `.env` is printed to standard output for inspection.
   
    *See [Configuration](#configuration) section for more deatails about environment variables.*

5. #### Database provisioning:
   - `PGPASSWORD` is exported and `pg_isready` is polled until readiness is reported.
   
   - For provisioning, the script connects to the PostgreSQL service using the hostname `postgres` for both `pg_isready` and `psql`. The `DENARO_DATABASE_HOST` value written to `.env` is not used by the entrypoint during provisioning.
   
   - The per node database is created if it does not already exist by connecting to the default `postgres` database. On first creation only, the application schema from `denaro/schema.sql` is imported into the new database.
   
   - `PGPASSWORD` is unset after provisioning completes.

6. #### Node launch:
   - The node is started with `exec python run_node.py` after configuration and provisioning are complete.
  
  ---

- #### **.dockerignore**:
  - A `.dockerignore` file has been added to exclude development files, registry artifact, secrets, and other non‑essential items from the build context.

---

## Configuration:

The `Dockerfile` does not copy the repository `.env` into the Docker image. In this Docker setup, the repository-level `.env` acts as a global source of shared variables consumed by Compose. *[[Ref. Dockerfile](#dockerfile)]*

Each node’s entrypoint generates its own application `.env` inside the container at startup using per-service variables, derived values, and shared inputs from either the global `.env` or the shared environment map. *[[Ref. Application .env Generation](#application-env-generation)]*

- #### **Environment variables configurable via `docker-compose.yml`**:
  - **`NODE_NAME`**: Identifies the node. It is used to derive the database name and internal self URL.
  
  - **`DENARO_NODE_HOST`** : Hostname or IP the node binds to or advertises. This is set to `0.0.0.0` by default so the service binds on all interfaces.
    
  - **`DENARO_NODE_PORT`**: Port number that the node listens on inside the container.
          
  - **`DENARO_DATABASE_HOST`**, **`POSTGRES_USER`**, **`POSTGRES_PASSWORD`**: 
      - Postgres database credentials. These are used by the entrypoint script for readiness checks and database provisioning.

      - By default, the database credentials are globally shared variables and `docker-compose.yml` expects them to be set in the global `.env`. However these can be overridden per-node or in the shared environment map. *See [Override conditions for environment variables](#override-conditions-for-environment-variables)*.

  - **`DENARO_BOOTSTRAP_NODE`**: Specifies either the selection criteria or a fixed address for the bootstrap-node.  *See [Bootstrap-node Discovery and Selection](#bootstrap-node-discovery-and-selection) for details*.
    - Accepted values:
      - **`'self'`**: Uses the node’s own internal address.
      
      - **`'discover'`**: Selects an address from the shared peer registry at `/registry/public_nodes.txt`.
      
      - The address of a Denaro Node that is reachable via the Internet or internal network.  
      
    - Note: *Fixed addresses must contain valid URL or IP Address and formatted as `'http(s)://<host>:<port>'`, with the port number only relevant when nessessary.*
        
  - **`ENABLE_PINGGY_TUNNEL`**: Enables a reverse tunnle via [Pinggy.io](https://www.pinggy.io)
    - When **`ENABLE_PINGGY_TUNNEL`** is set to **`'true'`**, the script will establish an SSH reverse tunnel via Pinggy's free tunnleing service (`free.pinggy.io:443`) to expose the node on the Internet. This is intended for testing public node behavior over the Internet. *[[Ref. Optional Public Node Tunnleing]](#optional-public-node-tunnleing)*.
    
  ---

- #### **Environment variables not configurable via `docker-compose.yml`**:
  - **`DENARO_DATABASE_NAME`**: Derived per-node as `denaro_${SANITIZED_NODE_NAME}` where **`SANITIZED_NODE_NAME`** replaces hyphens with underscores.
  
  - **`DENARO_SELF_URL`**: ~~Defaults to `http://${NODE_NAME}:${DENARO_NODE_PORT}`~~ and is replaced by the public URL that is assigneed via Pinggy.io when tunneling is enabled.
    - *This logic was later updated in commit [ecf57a8b](https://github.com/The-Sycorax/denaro/commit/ecf57a8bf1675b5788ce38519b1a020a3d90021c) to support external overrides and to prevent the script from overwriting the variable if it was already set. It now defaults to `http://${NODE_NAME}:${DENARO_NODE_PORT}` only when it's left unset.*

  ---

- #### Override conditions for environment variables:
  
  Denaro reads configuration exclusively from the application `.env` file. In this Docker setup the `Dockerfile` does not copy the repository `.env` into the Docker image. Instead, the entrypoint generates that file inside the container at startup. Compose-level values are inputs to the entrypoint, and not to the node itself.
  
  When generating the application `.env`, the entrypoint applies deterministic precedence across inputs. When multiple sources define the same value, the higher-precedence value overrides the lower-precedence value, and the resulting override is what the node reads from the final `.env`.

  - Override precedence from lowest to highest:
    - Values loaded via the global `.env`. These may be overridden by the shared environment map, explicit per-service entries, or values set by the entrypoint.

    - Keys merged from the shared environment map using `<<: *denaro-node-env` (only for services that merge it). These override the global `.env`.
    
    - Explicit per-node `environment` entries. These override both the global `.env` and shared map values.

    - Values set by `docker-entrypoint.sh`. For example: `DENARO_SELF_URL`, `DENARO_BOOTSTRAP_NODE`, and `DENARO_DATABASE_NAME`. These override all prior sources in the generated `.env`.

    - The node and underlying operating system have the highest effective precedence over runtime behavior.
