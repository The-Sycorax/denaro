**refactor(cpu_miner): implement inter-process coordination, use node Merkle root, and add max block limit**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Date**: September 10th, 2025

---

### Overview:

- This change updates the CPU miner to coordinate workers when one discovers a candidate block. 

- Workers are now shutdown using a shared event and a shared status value. This is to prevent duplicate or stale submissions.

- The miner now supports a configurable maximum blocks count and exits once the limit is reached.

- Endianness is standardized to little endian for all header fields and the nonce.

---

### Modified Functions:
- **`run_miner:`**
    - The function signature has been updated to accept `stop_event` and `submission_status`.
    
    - When a block is potentially solved, this function now performs the following:
      - The shared `stop_event` is set immediately so other workers exit quickly.
      - A single submission is sent to the `/push_block` endpoint with a timeout that scales with the number of transactions.
      - The node response is interpreted and `submission_status` is set to `STATUS_SUCCESS`, `STATUS_STALE`, or `STATUS_FAILED`.
    
    - The function now uses `mining_info['merkle_root']` from the node instead of computing a local Merkle root.
    
    - Header field serialization has been standardized:
        - The `timestamp` is now a 32 bit unsigned integer in little endian. 
        - The `difficulty` is now a 16 bit unsigned integer that represents tenths in little endian.
        - The `nonce` is now serialized in little endian.
    
    - The fallback previous hash for a no tip scenario has been changed to `(33_554_432).to_bytes(32, 'little').hex()`.
    
    - The function now reports the hashrate every 5,000,000 attempts and returns after 90 seconds to refresh work.
    
    - Header content and the list of transaction hashes are now printed when a block is found.

- **`worker_process:`**
    - The function signature has been updated to accept `stop_event` and `submission_status`.
    
    - The function now wraps `run_miner` and, on any unexpected exception, sets `submission_status` to `STATUS_FAILED` and signals `stop_event` to shutdown other workers.

- **`main:`**
    - For each mining round, this function now performs the following:
      - Retrieves mining info from `get_mining_info` with retries and basic error handling.
      - Creates a shared `Event` and a shared integer `Value('i')` for status reporting.
      - Spawns workers with distinct starting nonces spaced by the worker count to avoid collisions.
      - Waits while workers are alive, then terminates any workers that remain after the stop signal and joins them.

    - Additionally the function now handles status results as follows:
        - On `STATUS_SUCCESS`, increments the mined count. Exits when `--max-blocks` is reached. Otherwise waits briefly and refreshes work.
        - On `STATUS_STALE`, logs the stale result, waits briefly, and refreshes work.
        - On `STATUS_FAILED`, logs the failure, waits briefly, and refreshes work.
        - On `STATUS_PENDING`, treats the situation as a timeout and refreshes work.

    - A `--max-blocks` option has been added to stop after a specific number of successfully mined blocks.
    
    - Node URL handling has been normalized to ensure a trailing slash.

    - Logging has been improved for startup, hashrate, refresh actions, and the total mined count.
  
---

### Removed / Replaced Functions:
  - **`get_transactions_merkle_tree:`**
    - Removed. The miner now uses `mining_info['merkle_root']` from the node

---

### Constants:
  - **`STATUS_PENDING`**:
  - Status code for a pending or unknown submission state.

- **`STATUS_SUCCESS`**:
  - Status code returned when the node accepts the submitted block.

- **`STATUS_STALE`**:
  - Status code returned when the submitted block is stale because another block was accepted first.

- **`STATUS_FAILED`**:
  - Status code returned when submission fails due to rejection or network error.

### Other Changes:
- `miner.py` has been renamed to `cpu_miner.py` and moved to a `miner` directory

- The use of the `ENDIAN` constant has been replaced with explicit `'little'` literals.