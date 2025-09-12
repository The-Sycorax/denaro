**refactor(main): authenticated handshake, trustless sync, bulk block ingest, and security hardening**

**Contributer**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [c55a2bf0f479ef4a00ca6d76925583bd21a82095](https://github.com/The-Sycorax/denaro/commit/c55a2bf0f479ef4a00ca6d76925583bd21a82095)

**Date**: September 8th, 2025
 
---

### Overview & Security:
  - This change represents a complete networking and security refactor for `denaro/node/main.py`. Denaro nodes now uses authenticated, signed peer-to-peer requests, a structured handshake, and a hardened sync pipeline (with fork handling and pull/push sync). Resource limits and input validation have been tightened across the board. 
  
  - Several API endpoints have been replaced or split by trust level (peer vs. external client). A new security subsystem (caches, reputation, DNS protections, query cost accounting) underpins request handling and propagation. Startup and middleware flows have been simplified and made deterministic.

  - Signed peer requests are verified in a consistent format and protected against replay (timestamp + nonce). Outbound calls pin DNS; reserved/loopback/link-local ranges are always rejected, and private IPs are rejected when the local node is public. Private nodes may contact private IPs on the same network. 
  
  - Propagation runs under a global concurrency cap; expensive endpoints apply per-client query budgets. TTL caches reduce duplicate work. A global sync gate (`SyncStateManager`) ensures only one synchronization runs at a time.

---

### API Endpoints:
  - **New Enpoints:**
    - **POST** `/submit_tx` (public):
      - Public transaction intake that validates request payload, transaction hex size, and format. Inserts atomically via the pool, deduplicates, and propagates on success.
      - Rate limit: `30/minute`.
      - Returns:
        - `200 {ok:true, result:"Transaction has been accepted"}`
        - `200 {ok:false, error:"Transaction already present in pending pool"|"Mempool is full"|"Transaction failed validation"}`
        - `400 {ok:false, error:"Invalid transaction hex"}`

    - **POST** `/submit_block` (peer, signed):
      - Authenticated single-block submission that enforces height ordering, duplication checks, and bounded content size. Returns `409 sync_required` with the next expected ID when the receiver is behind.
      - Rate limit: `20/minute`.
      - Returns:
        - `200 {ok:true, result:"Block has been saved"}`
        - `409 {ok:false, error:"sync_required", result:{next_block_expected:<int>}}`
        - `400 {ok:false, error:"Invalid or malformed block"}`
        - `403 {ok:false, error:"Signed request required"}`
        - `413 {ok:false, error:"Block content too large"}`

    - **POST** `/submit_blocks` (peer, signed):
      - Authenticated bulk block ingestion that requires a continuous, strictly increasing ID range with per-block size bounds. Skips duplicates and continues; stops at the first invalid member.
      - Rate limit: `5/minute`.
      - Returns:
        - `200 {ok:true, result:"Successfully processed <N> blocks"}`
        - `200 {ok:false, error:"Invalid sequence"|"Block too large"}`
        - `400 {ok:false, error:"Request body must be a non-empty list"|"Invalid or malformed block"}`
        - `403 {ok:false, error:"Signed request required"}`
        - `413 {ok:false, error:"Block content too large"}`

    - **POST** `/get_peers` (peer, signed):
      - Authenticated peer list for discovery; excludes banned peers.
      - Returns:
        - `200 {ok:true, result:{peers:[{node_id,is_public,url?,reputation_score,...}, ...]}}`
        - `403 {ok:false, error:"Signed request required"}`

    - **GET** `/handshake/challenge` (public; client's subsequent response is signed):
      - Issues a one-time challenge and advertises local state: height, tip hash, node public key, and reachability.
      - Rate limit: `30/minute`.
      - Returns:
        - `200 {ok:true, result:{challenge,node_id,pubkey,url,is_public,height,last_hash}}`

    - **POST** `/handshake/response` (peer, signed):
      - Verifies the challenge response, authenticates the peer, negotiates sync direction (push or pull), and can request a push-sync window.
      - Rate limit: `30/minute`.
      - Returns:
        - `200 {ok:true, result:"Handshake successful."}`
        - `200 {ok:true, result:"sync_requested", detail:{start_block:<int>,target_block:<int>}}`
        - `400 {ok:false, error:"Invalid challenge or headers"}`
        - `403 {ok:false, error:"Signed request required"}`

    - **GET/HEAD** `/get_status` (public):
      - Returns current height, last block hash, and node ID for liveness/summary checks.
      - Returns:
        - `200 {ok:true, result:{height:<int|-1>, last_block_hash:<hex|null>, node_id:<str>}}`

    - **POST** `/check_reachability` (peer, signed):
      - An SSRF and DNS-rebinding safe reachability probe for a provided URL. Caches lookups and rejects unsafe address classes; public nodes reject private IPs (private nodes may reach private subnets).
      - Rate limit: `2/minute`.
      - Returns:
        - `200 {ok:true, result:{reachable:true|false, ip:"<ipv4>"}}`
        - `400 {ok:false, error:"Invalid or unresolvable URL"|"IP address is not globally routable and cannot be checked."}`
        - `403 {ok:false, error:"Signed request required"}`

    - **POST** `/get_mempool_hashes` (peer, signed):
      - Authenticated list of all pending transaction hashes to enable selective fetching.
      - Returns:
        - `200 {ok:true, result:["<64-hex>", ...]}`
        - `403 {ok:false, error:"Signed request required"}`

    - **POST** `/get_transactions_by_hash` (peer, signed):
      - Authenticated bulk fetch of transaction hex by hash with strict count and size validation (≤512).
      - Rate limit: `20/minute`.
      - Returns:
        - `200 {ok:true, result:["<txhex>", ...]}`
        - `400 {ok:false, error:"Invalid transaction hash list"}`
        - `403 {ok:false, error:"Signed request required"}`
        - `413 {ok:false, error:"Too many hashes requested"}`

  - **Modified Endpoints:**
    - **POST** `/push_tx` (peer, signed):
      - Now requires signed peer headers via `get_verified_sender`. Validates tx hex by length/charset, deserializes for sanity, checks pool capacity, deduplicates, inserts atomically, and records reputation signals.
      - Rate limit: `100/minute`.
      - Returns:
        - `200 {ok:true, result:"Transaction has been accepted"}`
        - `200 {ok:false, error:"Transaction already present"|"Mempool is full"|"Transaction failed validation"}`
        - `400 {ok:false, error:"Invalid transaction hex"}`
        - `403 {ok:false, error:"Signed request required"}`

    - **POST** `/push_block` (public, miner/legacy):
      - Retains an unauthenticated miner path with stricter size and duplication checks, improved error handling, and limited processing.
      - Rate limit: `12/minute`.
      - Returns:
        - `200 {ok:true, result:"Block has been saved"}`
        - `200 {ok:false, error:"Invalid block"|"Duplicate block"|"Out-of-sequence block"}`
        - `400 {ok:false, error:"Invalid or malformed block"}`
        - `413 {ok:false, error:"Block content too large"}`

    - **GET** `/get_mining_info` (public):
      - Now loads all pending hashes, deserializes candidates up to a cap, and performs topological selection so parents always precede children. Produces a coherent block template with hashes, hexes, and the merkle root.
      - Rate limit: `15/minute`.
      - Returns:
        - `200 {ok:true, result:{pending_transactions:[<hex>...], hashes:[<hex>...], merkle_root:"<hex>"}}`

    - **GET** `/get_address_info` (public):
      - Now adds strict address validation and a per-IP query-cost check over offset and limit. Returns balance, spendable outputs, and paged recent transactions with optional pending and verification views.
      - Rate limit: `8/second`.
      - Returns:
        - `200 {ok:true, result:{balance:<int>,spendable_outputs:[...],transactions:[...],...}}`
        - `400 {ok:false, error:"Invalid address or parameters"}`
        - `429 {ok:false, error:"Query cost limit exceeded. Try again later."}`

    - **GET** `/get_block` (public):
      - Now validates numeric heights via the new InputValidator class and supports full transactions.
      - Rate limit: `30/minute`.
      - Returns:
        - `200 {ok:true, result:{...block...}}`
        - `200 {ok:false, error:"Not found"}`
        - `400 {ok:false, error:"Invalid selector"}`

    - **GET** `/get_blocks` (public):
      - Now enforces query-cost limits for offset and limit.
      - Rate limit: `10/minute`.
      - Returns:
        - `200 {ok:true, result:[{...block...}, ...]}`
        - `400 {ok:false, error:"Invalid parameters"}`
        - `429 {ok:false, error:"Query cost limit exceeded. Try again later."}`

    - **GET** `/get_nodes` (public):
      - Now returns only public, recent peers with a reputation score. Private peers are withheld and the list size is capped at 100.
      - Returns:
        - `200 {ok:true, result:[{node_id,is_public,url,reputation_score}, ...]}`

    - **GET** `/get_transaction` (public):
      - Now normalizes response shape. Optional “nice” formatting is retained.
      - Rate limit: `8/second`.
      - Returns:
        - `200 {ok:true, result:{...transaction...}}`
        - `200 {ok:false, error:"Not found"}`
        - `400 {ok:false, error:"Invalid transaction hash"}`

    - **GET** `/get_pending_transactions` (public):
      - Now returns recent pending transactions in hex with existing limit semantics.
      - Returns:
        - `200 {ok:true, result:["<txhex>", ...]}`

    - **GET** `/sync_blockchain` (public):
      - Now fronts `_sync_blockchain` with stronger state enforcement. Refuses when a sync is already active and schedules the background sync task.
      - Rate limit: `10/minute`.
      - Returns:
        - `200 {ok:true, result:"Synchronization process has been initiated."}`
        - `503 {ok:false, error:"Node is already synchronizing"|"Maximum concurrent syncs reached"}`

    - **GET** `/` (root):
      - Now returns `{version, unspent_outputs_hash}` with encoder and validation integration aligned to the refactor.
      - Returns:
        - `200 {ok:true, result:{version:<str>, unspent_outputs_hash:<hex>}}`

  - **Removed Endpoints:**
    - **GET** `/add_node`:
      - Replaced by identity-based discovery and the handshake flow.
  
---

### Non-Endpoint Top-Level Functions:
  - **New:**
    - `rate_limit_key_func`:
      - Chooses the rate-limit key. Uses `x-node-id` for authenticated requests and the client IP otherwise.
    
    - `validate_url_for_connection`:
      - Performs outbound URL vetting with DNS resolution and rebinding protection.
    
    - `_push_sync_to_peer`:
      - Batches and pushes missing blocks to a lagging peer with bounded size and retries. Re-submits the trigger block at the end.
    
    - `check_peer_and_sync`:
      - Queries a peer and initiates a pull when the peer’s chain is longer.
    
    - `get_verified_sender`:
      - Verifies signed headers with timestamp and nonce, prevents replays, updates peer metadata, and bans or penalizes on failures.
    
    - `do_handshake_with_peer`:
      - Client-side handshake that performs challenge and response, exchanges state, negotiates sync direction, and updates the peer record.
    
    - `periodic_peer_discovery`:
      - Gossip-based discovery with resilient scheduling and verification through the handshake.
    
    - `is_url_local`:
      - Resolves a hostname to IPv4 and classifies address ranges to detect local or private networks.
    
    - `check_own_reachability`:
      - Bootstrap probe to determine public reachability and update node metadata.
    
    - `periodic_update_fetcher`:
      - Periodically probes peers for longer chains and mempool differences and selectively fetches missing transactions.
    
    - `process_and_create_block`:
      - Pre-validation wrapper for block ingestion that deserializes transactions, applies size bounds, and invokes block creation.
    
    - `handle_reorganization`:
      - Detects forks, finds a bounded common ancestor, rolls back, and re-queues valid non-coinbase orphaned transactions.
    
    - `handle_unreachable_peer`:
      - Centralized cleanup when a peer becomes unreachable with non-punitive removal from the active set.
    
    - `shutdown`:
      - Gracefully stops periodic tasks and closes shared clients.
    
    - `unhandled_exception_handler`:
      - Uniform 500 handler that logs to the security monitor and returns `{"ok": false, "error": "Internal Server Error"}`.
  
  - **Modified:**
    - `propagate`:
      - Now propagates to peers with reputation checks, limits parallel requests using a semaphore, and triggers push-sync when needed.
    
    - `_sync_blockchain`:
      - Sync driver updated to include fork checks at the tip, reorganization handling, and controlled pulls under the global sync gate.
    
    - `startup`:
      - Now initializes a shared HTTP client, security components, node identity, `NodesManager`, resets pending state, and launches periodic maintenance tasks.
    
    - `middleware`:
      - Normalizes request paths and schedules background propagation of stale transactions. CORS is configured via middleware.
    
    - `propagate_old_transactions`:
      - Behavior retained. Now integrates with the shared propagation path and security components.

  - **Removed:**
    - `create_blocks`:
      - Superseded by `process_and_create_block` and the authenticated submit endpoints.
    
    - `exception_handler`:
      - Replaced by `unhandled_exception_handler`.
    
    - `sync_blockchain`:
      - Consolidated into `_sync_blockchain` under the global sync gate.

---

### New Classes:
  - `TimeBasedCache`:
    - Thread-safe TTL cache for hot-path deduplication and lookups.
    - **Functions:**
      - `__init__`: Configures size and TTL.
      - `get`: Returns a cached value and expires entries on access.
      - `put`: Inserts entries and evicts via an access deque when the size is exceeded.
      - `contains`: Checks for presence using `get`.
      - `clean`: Manually sweeps expired entries.
  
  - `HandshakeChallengeManager`:
    - Manages one-time challenges with expiry and cleanup.
    - **Functions:**
      - `__init__`: Configures TTL and internal state.
      - `start`: Starts the periodic cleanup task.
      - `stop`: Stops the cleanup task.
      - `_periodic_cleanup`: Removes expired challenges on a fixed schedule.
      - `cleanup`: Immediately removes expired challenges.
      - `create_challenge`: Creates and returns a 32-byte random hex challenge.
      - `verify_and_consume_challenge`: Verifies that a challenge exists and consumes it in a single step with TTL enforcement.
  
  - `BoundedPeerSyncTracker`:
    - Tracks in-progress push-syncs for a bounded peer set.
    - **Functions:**
      - `__init__`: Initializes capacity and internal locks.
      - `add_peer`: Admits a peer or evicts the oldest when the set is full.
      - `remove_peer`: Removes a peer from the active sync set.
      - `is_syncing`: Indicates whether a peer currently has an active sync.
  
  - `SyncStateManager`:
    - Global sync gate that prevents concurrent sync conflicts.
    - **Functions:**
      - `__init__`: Configures counters and locks.
      - `acquire_sync`: Async context manager that toggles `is_syncing` and a concurrency counter.
  
  - `InputValidator`:
    - Implements strict validation for hex strings, heights, addresses, and transaction data.
    - **Functions:**
      - `validate_hex`: Enforces even length, hex character set, and size bounds.
      - `validate_block_height`: Ensures the requested height is within the current or allowed ahead range.
      - `validate_address`: Validates length and envelope by pattern.
      - `validate_transaction_data`: Applies combined size and hex checks for transaction bodies.
  
  - `AuthenticatedRequestValidator`:
    - Verifies signed requests and blocks replays.
    - **Functions:**
      - `__init__`: Configures maximum age and the nonce cache.
      - `validate_request`: Checks required headers (`x-node-id`, `x-public-key`, `x-signature`, `x-timestamp`, `x-nonce`), enforces clock skew and nonce uniqueness, canonicalizes body and headers, and verifies the signature.
  
  - `DNSSafeHTTPClient`:
    - DNS-pinning and allowlist-aware resolver for outbound HTTP.
    - **Functions:**
      - `__init__`: Configures a TTL-based DNS cache and timeouts.
      - `validate_and_resolve`: Enforces scheme, resolves to IPv4, rejects unsafe IP ranges, and caches results.
  
  - `PeerViolation`:
    - Dataclass that records peer reputation events.
    - **Functions:**
      - (dataclass fields) `timestamp`, `violation_type`, `severity`, `details`.
  
  - `PeerReputationManager`:
    - Tracks peer scores, violations, and bans.
    - **Functions:**
      - `__init__`: Initializes configuration and state.
      - `record_violation`: Applies severity-based penalties and bans on threshold.
      - `record_good_behavior`: Applies small rewards for compliant behavior.
      - `is_banned`: Checks ban status.
      - `get_score`: Returns the current peer score.
      - `cleanup_old_violations`: Performs TTL-based decay and garbage collection.
  
  - `QueryCostCalculator`:
    - Soft limiter for expensive queries.
    - **Functions:**
      - `__init__`: Configures an hourly budget per identifier.
      - `check_and_update_cost`: Applies an offset and limit cost model and raises an error when the budget is exhausted.
  
  - `SecurityMonitor`:
    - Aggregates security metrics and threshold checks.
    - **Functions:**
      - `__init__`: Initializes metric counters.
      - `log_event`: Records an event by subtype.
      - `get_metrics`: Returns a metrics snapshot.
      - `check_thresholds`: Emits coarse alerts when spikes occur.
  
  - `SafeTransactionPool`:
    - Lock-protected mempool with atomic DB insertion and bounded size.
    - **Functions:**
      - `__init__`: Configures pool size and insertion timestamps.
      - `add_transaction`: Deduplicates, evicts the oldest under pressure, and inserts into the DB atomically with rollback on failure.
      - `remove_transactions`: Removes a bulk set of transactions by hash.
  
  - `SecureNodeComponents`:
    - Aggregates security and capacity primitives and runs periodic cleaners.
    - **Functions:**
      - `__init__`: Constructs caches, validators, reputation tracking, and semaphores.
      - `startup`: Starts challenge cleanup and periodic sweeps.
      - `shutdown`: Stops tasks and flushes resources.
      - `_periodic_cleanup`: Performs timer-driven cache cleanup and threshold checks.

---

### Modified Classes:
  - `CustomJSONEncoder`:
    - Extends serialization to explicitly handle `Decimal` and `datetime` in ISO-8601 form before delegating to the base encoder.
    - **Functions:**
      - `default`: Serializes added types and falls back to the parent implementation.

---

### Constants:
  - `MAX_BLOCKS_PER_SUBMISSION`: Upper bound for blocks in a single bulk submit.
  - `MAX_BLOCK_CONTENT_SIZE`: Per-block content size cap.
  - `MAX_PEERS`: Bound on active peer set size.
  - `MAX_CONCURRENT_SYNCS`: Global limit on concurrent sync operations.
  - `MAX_TX_FETCH_LIMIT`: Cap for bulk transaction retrieval.
  - `MAX_PENDING_POOL_SIZE`: Mempool size cap for pending transactions.
  - `CONNECTION_TIMEOUT`: Shared HTTP client timeout.
  - `VALID_HEX_PATTERN`, `VALID_ADDRESS_PATTERN`: Input validation patterns.
  - `MAIN_DENARO_NODE_URL`, `DENARO_SELF_URL`: Bootstrap and self-reachability configuration.

---

### Breaking Changes:
  - Removes `GET /push_tx`. Use `POST /push_tx` for peer-authenticated submissions or `POST /submit_tx` for public submissions.
  - Removes `GET /push_block`. Use `POST /push_block` for miners or the authenticated `/submit_block(s)` paths.
  - Removes `GET /add_node`. Discovery is identity-based via the handshake.
  - Switches propagation to peer identities rather than raw URLs.
  - Unifies exception handling under `unhandled_exception_handler`.

---

### Other Changes:
  - Added `get_all_pending_transaction_hashes` to `database.py`. This function fetches the hashes of all transactions currently in the pending pool.
  - Added `get_pending_transaction_count` to `database.py`. This function returns the total number of transactions in the pending pool. 
  - Added `remove_all_pending_transactions` to `database.py`. This function truncates the entire `pending_transactions` table.
  - The `get_blocks` function in `database.py` no longer uses `OLD_BLOCKS_TRANSACTIONS_ORDER`.
  - Moved `schema.sql` to `denaro` sub-directory.
  