**refactor(api): restructure and improve API endpoints, naming, and documentation**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [8aef4021946e7ffa557b9dbca779ea449aad32a8](https://github.com/The-Sycorax/denaro/commit/8aef4021946e7ffa557b9dbca779ea449aad32a8)

**Date**: January 26th, 2026

---

## Overview:

This refactor introduces several improvements to Denaro's API layer, including standardized
endpoint naming, expanded peer and node information, along with updated documentation across
all endpoints.

- The docstrings for all API endpoints have been updated to maintain accurate documentation
  of their behavior, query parameters, and request/response structure. This also improves
  the auto-generated OpenAPI spec and interactive FastAPI docs (Swagger UI).

- A new `FlagParameter` type has been added for handling flag-style query parameters.
  For example, `?pretty` can now be used instead of `?pretty=true`.

- The root endpoint `/` now returns the node version, GitHub Repository URL, and URL of the
  node's interactive Swagger UI.

- The block submission and propagation endpoints have been renamed to align with the
  established naming convention of other endpoints:
    - `/push_block` --> `/submit_block`
    - `/submit_block` --> `/push_block`
    - `/submit_blocks` --> `/push_blocks`

- The `/get_peers` endpoint has been refactored as an unauthenticated endpoint that now
  provides detailed information and statistics of known peers. It now supports optional
  parameters for filtering public, private, or banned peers.

- The `/get_status` endpoint has been expanded to provide more information about the node.

- Peer version tracking has been introduced and rate limits have been updated across multiple
  endpoints. 

- This refactor increments Denaro's version from `2.0.1` to `2.0.2`.

---

## API Endpoints:

- Endpoints now use the new `FlagParameter` type for boolean parameters.

- ### **Modified Endpoints:**
    - **GET** `/` (public, root):
      - Removed unnecessary `unspent_outputs_hash` from response.
      
      - Response now includes the node version, Github Repository URL, and the API Documentation URL of the node.
      
      - **Updated Reponse:**
        - `200 OK`:
          ```json
          {
            "node_version": <str>,
            "github_repository": "https://github.com/The-Sycorax/denaro",
            "api_docs": <str|null>
          }
          ```
        - If `DENARO_SELF_URL` is None then `api_docs` will be `null`

    ---

    - **GET** `/get_status` (public):
      - Updated Rate Limit: `60/minute`.
      
      - HTTP Method changed from `GET/HEAD` to `GET`.
      
      - Added `pretty` query parameter.
      
      - Response has been expanded to include `node_id`, `pubkey`, `url`, `is_public`, and `uptime_seconds`.
      
      - **Updated Response:**
        - `200 OK`:
          ```json
          {
            "ok": true,
            "result": {
              "node_id": "<hex>",
              "pubkey": "<hex>",
              "url": "<str>",
              "is_public": <bool>,
              "node_version": "<str>",
              "height": <int>,
              "last_block_hash": "<hex>",
              "uptime_seconds": <int>
            }
          }
          ```

    ---

    - **GET/POST** `/get_peers` (public):
      - Added Rate Limit: `60/minute`.
      
      - ~~Updated HTTP method changed from `POST` to `GET`.~~
        - HTTP method was updated in commit [aff5b45c](https://github.com/The-Sycorax/denaro/commit/aff5b45c0a460151370f77cc84263d7b64d419dc) to allow for both `GET` and `POST` requests.
      
      - No longer requires an authenticated signed request.
      
      - Returns a list of active peers that are connected to the node, along with information related to them. Includes query parameters for showing peer statistics, and for filtering between public, private, and banned peers.
    
      - **Query Parameters:**
        - `show_stats` (boolean, optional): Includes peer statistics.
        
        - `public` (boolean, optional): Filters for public peers only.
        
        - `private` (boolean, optional): Filters for private peers only.
        
        - `show_banned` (boolean, optional): Includes banned peers.
        
        - `pretty` (boolean, optional): Formats JSON output for readability.
  
      - **Updated Response:**
          - `200 OK`:
              ```json
              {
                "ok": true,
                "result": {
                  "peers": [
                    {
                      "node_id": "<hex>",
                      "pubkey": "<hex>",
                      "url": "<str>",
                      "is_public": <bool>,
                      "node_version": "<str>",
                      "reputation_score": <int>,
                      "last_seen": <timestamp>
                    }
                  ],
                  "peer_stats": {
                    "total_peers": <int>,
                    "public_peers": <int>,
                    "private_peers": <int>,
                    "connectable_peers": <int>,
                    "recent_peers": <int>
                  }
                }
              }
              ```
  
    ---

    - **POST** `/push_block` (peer, signed):
      - Renamed from `/submit_block`.
      
      - Now exclusively for authenticated peer-to-peer block propagation.
      
      - **Compatibility**: 
        - Legacy miner compatibility was added in commit [bebc6e66](https://github.com/The-Sycorax/denaro/commit/bebc6e66750967e1db28724c4f5c4ca21d4dfe22). It ensures that if an unsigned request is sent (e.g. from a legacy miner), it is automatically routed to the `/submit_block` logic.
    
    ---

    - **POST** `/push_blocks` (peer, signed):
      - Renamed from `/submit_blocks`.
      - Now exclusively for authenticated bulk block sync between peers.
    
    ---

    - **POST** `/submit_block` (public, miners):
      - Renamed from `/push_block`. 
      
      - Now used as an unauthenticated endpoint for miners to submit newly mined blocks.
      
      - On success, propagates blocks to peers via `/push_block`.

    ---

    - **GET** `/get_block` (public):
      - Updated Rate Limit: `60/minute`.
      
      - Removed `block` query parameter.
      
      - Added `id` and `hash` query parameters.
      
      - Endpoint has been updated to return a block by it's `hash` or by it's `id` (block height) via the new query parameters.

    ---

    - **GET** `/get_blocks` (public):
      - Updated Rate Limit: `60/minute`.
      
      - Removed call to `security.query_calculator.check_and_update_cost()`. This was done to prevent clients and other nodes from receiving a `429 Too Many Requests` response error when requesting blocks.

    ---

    - **GET** `/get_transaction` (public):
      - Renamed `tx_hash` query parameter to `hash`.
      
      - Removed unused `verify` parameter.

    ---

    - **GET** `/get_mining_info` (public):
      - Updated Rate Limit: `60/minute`.

    ---

    - **GET** `/get_pending_transactions` (public):
      - Updated Rate Limit: `60/minute`.
      
      - Added `pretty` query parameter.

    ---

    - **GET** `/handshake/challenge` (public):
      - Added `pretty` query parameter.
      
      - Now captures and stores `x-node-version` from headers

    ---

- ### **Removed Endpoints:**
    - **GET** `/get_nodes`

---

## Other Changes:

- ### **`denaro/node/main.py`**:

  - Added `FlagParameter` class for boolean flag query parameters. Accepts `?param` (presence) and `?param=true` as `True`.
  
  - Added OpenAPI docstrings to all API endpoints with parameter descriptions and return types.
  
  - Updated FastAPI app initialization with `title`, `description`, and GitHub repository URL.
  
  - Updated various variables and logic to align with the new API updates.

  - **Logging adjustments**:
    - Some `logger.warning()` calls for non-critical conditions changed to `logger.info()`.
    
    - Unhandled exception handler changed from `logger.error()` to `logger.critical()`.
    
    - Stack traces are now logged via `logger.debug()` instead of `traceback.print_exc()`.
  
  - JSON pretty-print indentation changed from 4 spaces to 2 spaces.

  ---

- ### **`denaro/node/nodes_manager.py`**:
  - Added `NODE_VERSION` import from `denaro/constants.py`
  
  - Added `version` argument to `add_or_update_peer` method.
  
  - Added `x-node-version` header to outgoing signed requests.
  
  - Renamed `submit_block()` to `push_block()` in `NodeInterface`.
  
  - Renamed `submit_blocks()` to `push_blocks()` in `NodeInterface`.
  
  - Changed `get_peers()` from authenticated `_signed_request` to unauthenticated `GET`.

  ---

- ### **`denaro/logger.py`**:
  - Updated `Console` initialization to include `width=256` and `force_terminal=True`. This was done to allow for proper log formatting and coloring in Docker.

  ---

- ### **`denaro/constants.py`**:
  - `NODE_VERSION` incremented from `'2.0.1'` to `'2.0.2'`.
  
  ---

- ### **`run_node.py`**:
  - Replaced `dotenv_values` config loading with direct imports from `denaro.constants`.
  
  - Removed local `DENARO_NODE_HOST` and `DENARO_NODE_PORT` variable assignments.

  ---

- ### **`miner/cpu_miner.py`**:
  - Updated block submission URL from `/push_block` to `/submit_block`.

  ---

- ### **`miner/cuda_miner.py`**:
  - Updated block submission URL from `/push_block` to `/submit_block`.

  ---

- ### **`docker/docker-entrypoint.sh`**:
  - Fixed `LOG_FORMAT` default value (removed `UTC` suffix).
