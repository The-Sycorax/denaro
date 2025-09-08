**refactor(nodes_manager): improved peer registry, signed peer requests, better persistence and reachability**

**Contributer**: The-Sycorax (https://github.com/The-Sycorax)

**Date**: September 8th, 2025

---

### Overview:
  - This change replaces URL lists and `pickledb` with a JSON-backed peer registry (`nodes.json`) keyed by NodeID. Each entry stores the peer’s `pubkey`, `url`, `last_seen`, and `is_public` status, providing a structured and persistent registry for discovery.  

  - The built-in HTTP client is removed, and callers now inject a shared `httpx.AsyncClient`. This makes transport handling explicit and decouples client management from peer logic.  

  - All state-changing peer requests are signed using a canonical payload, timestamp, and nonce. Identity headers (`x-node-id`, `x-public-key`, `x-signature`, `x-timestamp`, and `x-nonce`) standardize authentication, while optional `x-denaro-*` headers extend flexibility. The `x-peer-url` header is included when `SELF_URL` is public or when both peers appear to reside on the same LAN.  

  - Peer propagation selects only from nodes that have been seen recently and that advertise a valid URL. This ensures propagation flows through active, reachable peers rather than stale registry entries.  

  - HTTP handling is simplified: transport errors are re-raised, `409` responses act as synchronization hints, and successful responses return parsed JSON. Non-`409` HTTP statuses and JSON decode failures yield `None`, leaving error handling to the caller. Legacy features such as chunked streaming and the hex-size guard are removed.  

  - **Bootstrap change:** `NodesManager.init` no longer seeds a default peer when storage is empty. A configurable `MAIN_DENARO_NODE_URL` replaces implicit seeding, but initialization itself does not automatically populate peer state.  


---

- ### `NodesManager`:
    - Peer registry and transport utility that is responsible for:
        - Persisting a NodeID-keyed peer map to `nodes.json`.
            - New format: `{"peers": { "<node_id>": {"pubkey","url","last_seen","is_public"} }}`.
        - Validating and normalizing peer records:
            - Skips self.
            - Normalizes and sanitizes URLs.
            - Enforces the `MAX_PEERS_COUNT` capacity limit.
            - Updates `last_seen` value on upsert and on activity.
        - Providing read views for selection:
            - Returns recent peers filtered by `ACTIVE_NODES_DELTA`, sorted by `last_seen` (descending).
            - Produces a propagation set limited to recent peers that have a URL
        - Managing public reachability metadata:
            - Records the local node’s public/ingress status via `set_public_status(is_public: bool)`.
        - Wrapping HTTP requests using a shared `httpx.AsyncClient`, with explicit error semantics:
            - Re-raises network/transport errors (`httpx.RequestError`) to signal unreachability.
            - Treats HTTP `409` as a non-fatal synchronization hint.
            - Returns **`None`** on other HTTP status codes (non-2xx, non-409) and on JSON decoding failures.

    - **New Methods:**
        - `purge_peers`:
            - Clears the registry and persists an empty `{"peers": {}}`.
        - `add_or_update_peer`:
            - Upserts peers by NodeID, ignores self, normalizes the URL, updates `last_seen` value, and enforces `MAX_PEERS_COUNT`. Returns `True` when a peer is newly added.
        - `update_peer_last_seen`:
            - Updates a peer’s `last_seen` value and persists the change.
        - `get_peer`:
            - Returns a single peer by NodeID.
        - `get_all_peers`:
            - Returns all peer records (each includes `node_id`).
        - `get_propagate_peers`:
            - Selects peers up to `limit` from the **recent + has URL** set for outbound propagation.
        - `set_public_status`:
            - Records whether the local node is publicly reachable.
        - `remove_peer`:
            - Removes a peer by NodeID and persists. Returns `True` on removal.

    - **Modified Methods:**
        - `init`:
            - Now accepts the local NodeID. Loads `{"peers": {...}}` from JSON and creates the file if it does not exist. **Does not seed any default peer.**
        - `sync`:
            - Now persists the full peer map to `nodes.json`, replacing `pickledb.set(...)`.
        - `request`:
            - Now uses the injected `httpx.AsyncClient`. Transport errors are re-raised, `409` is treated as non-fatal, and the method returns `response.json()`. **On non-409 HTTP status codes or JSON decode failure, returns `None`.** Chunked streaming and the hex-size guard are removed.
        - `get_recent_nodes`:
            - Now returns peer objects filtered by `ACTIVE_NODES_DELTA` and sorted by `last_seen` (descending). Previously returned URL strings.

    - **Removed/Replaced Methods:**
        - `add_node` → replaced by `add_or_update_peer`.
        - `get_nodes` → replaced by `get_all_peers`,
        - `get_propagate_nodes` → replaced by `get_propagate_peers`.
        - `get_zero_nodes` → removed.
        - `get_last_message`, `update_last_message` → removed (`last_seen` now tracks activity).
        - `clear_old_nodes` → removed (replaced by “recent” views).
        - `is_node_working` → removed (reachability is inferred from request results).

---

- ### `NodeInterface`:
    - Signed peer request layer with endpoint helpers that are responsible for:
        - Building and signing canonical JSON payloads for state-changing requests.
          - Attaches identity headers: 
            - `x-node-id`, `x-public-key`, `x-signature`, `x-timestamp`, `x-nonce`.        
          - Supports optional `x-denaro-*` headers for chain context.
        
        - Controlling self-URL advertisement:
          - Includes `x-peer-url` only when `SELF_URL` is public, or when both peers appear local (LAN) via IP classification.
        
        - Providing various endpoint helpers used by the node.

        - Delegating plain GET requests to `NodesManager.request` and returning raw JSON.


    - **New Functions:**
        - `_signed_request`:
            - Builds a canonical payload, signs it, and attaches identity and optional `x-denaro-*` headers. Includes `x-peer-url` only when allowed (public `SELF_URL` or local-to-local).
        - `is_url_local`:
            - Resolves hostnames and classifies IPs (private, loopback, and link-local) to determine whether the local URL should be advertised.
        - `push_tx`:
            - Submits a signed transaction to a peer.
        - `submit_block`:
            - Submits a signed single block.
        - `submit_blocks`:
            - Submits a signed batch of blocks. A continuous range is expected.
        - `get_status`:
            - Retrieves remote liveness and summary.
        - `get_peers`:
            - Retrieves the remote peer list (signed).
        - `handshake_challenge`:
            - Initiates a handshake challenge (unsigned).
        - `handshake_response`:
            - Sends a signed challenge response and includes local `{height, last_hash}` via headers.
        - `check_peer_reachability`:
            - Performs a signed probe to `/check_reachability` and returns a boolean.
        - `get_mempool_hashes`:
            - Retrieves pending transaction hashes (signed).
        - `get_transactions_by_hash`:
            - Retrieves transactions by hash in bulk (signed).

    - **Modified Functions:**
        - `__init__`:
            - Now stores a shared HTTP client and DB handle. Per-instance base-URL derivation is removed.
        - `get_block`:
            - Performs a plain GET via `NodesManager.request` and returns raw JSON (no `result` unwrapping).
            - **Signature changed:** now accepts `block: str` (was `block_no: int`). The `full_transactions` option is removed.
        - `get_blocks`:
            - Performs a plain GET via `NodesManager.request`. Callers handle errors; there is no `result` unwrapping.

    - **Removed/Replaced Functions:**
        - `request` → replaced by `_signed_request` for signed POST requests. GET requests go through `NodesManager.request`.
        - `get_nodes` → replaced by `get_peers`.

---

- ### Constants:
    - `MAX_PEERS_COUNT = 200` (previously `MAX_NODES_COUNT = 100`).
    - `MAIN_DENARO_NODE_URL` (from `MAIN_DENARO_NODE`) is introduced. **Note:** not used by `NodesManager.init` for seeding.
    - `SELF_URL` (from `DENARO_SELF_URL`) now gates `x-peer-url` exposure.
    - `INACTIVE_NODES_DELTA` is removed.

---

- ### Breaking Changes:
    - The persistence format is now `{"peers": { "<node_id>": {"pubkey", "url", "last_seen", "is_public"} }}`. The previous URL-list plus `last_messages` layout is removed.
    - The manager no longer owns an HTTP client. Timeouts and retries must be configured on the injected `httpx.AsyncClient`.
    - `NodeInterface.get_block` no longer supports `full_transactions`, no longer unwraps `result`, and **now accepts `block: str`** instead of `block_no: int`.
    - `get_recent_nodes` now returns structured peer objects instead of URL strings.
    - Propagation selection now uses only recent peers with a URL and is capped by `limit` (default 10).
    - The legacy client header `Sender-Node` is removed. Identity is now carried via `x-node-id`, `x-public-key`, `x-signature`, `x-timestamp`, and `x-nonce`.
    - The `NodeInterface.base_url` attribute is removed.
    - Bootstrap seeding has been removed. Any initial peers must be provided by higher-level bootstrapping logic.
