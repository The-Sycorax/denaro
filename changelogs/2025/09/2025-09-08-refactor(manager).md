**refactor(manager): implement new monetary policy, better consensus logic, standardized Merkle, and stricter validation**

**Contributer**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [c55a2bf0f479ef4a00ca6d76925583bd21a82095](https://github.com/The-Sycorax/denaro/commit/c55a2bf0f479ef4a00ca6d76925583bd21a82095)

**Date**: September 8th, 2025

---

### Overview:
  - This change consolidates Denaro’s consensus logic into a single, deterministic path. It now uses a fixed **512-block** retarget period with bounded adjustments, introduces a power-of-two halving schedule, standardizes Merkle computation over sorted transaction hashes, tightens timestamp and size rules, normalizes mempool hashing, and persists the header-encoded difficulty.
  
  - Height-gated special cases and ad hoc historical exceptions are removed. Stored difficulty now reflects the header’s encoded value rather than a local recomputation.
  
  - Block persistence performs a grouped commit with best-effort cleanup on failure (removal of the current block row).

---

### New Monetary Policy:
  - The new monetary policy for Denaro has been chosen for its optimal balance of a scarce total supply, frequent halving events, and mathematical elegance; with all block subsidy parameters being powers-of-two:
    - **Initial Reward: 64 DNR** (2^6)
    - **Halving Interval: 262,144** blocks (2^18) 
      - Targets ~2.5 years per halving
    - **Maximum halvings: 64** (2^6)
    - **Estimated Emission Lifespan: ~160 years** (64 halvings)      
    - **Based on this policy, we can calculate the maximum total supply for Denaro to be capped at: 33,554,432 DNR** (2^25)
    
  - The previous logic has been replaced with a single policy that caps issuance under the new schedule and removes the prior piecewise logic.
  
  - The block reward used in `create_block` comes from this schedule and applies to the current height at block creation. Fee calculation continues to sum only regular (non-coinbase) transactions.

### Other Major Logic Changes:
  - Difficulty re-targeting runs strictly at **512-block** boundaries with a bounded ratio of **[0.25, 4.0]** for stability. Inside a window, the previously stored on-chain difficulty is honored.
  
  - Merkle roots are now computed from sorted transaction hash hex strings, and are concatenated and hashed once with SHA-256. Height gates and block-specific exceptions are removed.
  
  - Timestamp validation now requires strictly increasing block times and rejects future timestamps exceeding **+120 seconds**.
  - Size enforcement now applies to both the header/content bytes *(hex length)* and the aggregate size of regular transactions. Oversized candidates are rejected early.
  
  - In-block safety now detects duplicate inputs by `(tx_hash, index)` pairs and enforces exact UTXO availability for all inputs. Per-tx double-spend checks remain disabled during verification.

  - Block persistence now performs a grouped commit *(block, coinbase, regular transactions, UTXO updates, pending removals)* under a single try/except. On any failure it deletes the current block row.

---

### Modified Functions:
  - **`difficulty_to_hashrate:`**
    - This function now derives the integer hex digit with `floor(difficulty)` and the fractional remainder with `difficulty % 1`, then computes hashrate using `Decimal` arithmetic.
    - It returns a **Decimal** (annotation states `-> int`); it retains deterministic boundary behavior across fractional steps and preserves the intended scale.

  - **`hashrate_to_difficulty:`**
    - This function now guards `hashrate <= 0` by returning the start difficulty, computes the integer hex digit via `floor(log(hashrate, 16))`, and derives the intra-bucket ratio.
    - It scans decimal tenths and returns on the first threshold where the coefficient meets or exceeds the ratio; it retains a monotonic, stable mapping at bucket edges.

  - **`calculate_difficulty:`**
    - This function now retargets strictly every **512** blocks; it returns the start difficulty before the first full period and the current on-chain difficulty within a period. At period boundaries it computes the average block time over the full window.
    - It clamps the adjustment ratio to **[0.25, 4.0]**, converts difficulty → hashrate, scales by the ratio, and converts back with the canonical mapping; it prints a concise adjustment summary.

  - **`get_transactions_merkle_tree:`**
    - This function now collects each transaction’s hash hex, sorts the hashes, concatenates the hex strings, and applies a single SHA-256 to the concatenation.
    - It produces a deterministic Merkle root independent of insertion order and removes all height-based exceptions.

  - **`split_block_content:`**
    - This function now infers version `1` from total length and otherwise reads a single version byte without strict assertions.
    - It returns `(previous_hash, address, merkle_tree, timestamp, difficulty, random)` with difficulty decoded from `u16 / 10`; it preserves field order and width.

  - **`check_block_is_valid:`**
    - This function now computes the candidate hash once and reuses it to compare against the PoW target.
    - It retains the fractional hex-digit matching rule and the genesis/no-tip fast path (returns `True` when no prior tip exists).

  - **`clear_pending_transactions:`**
    - This function now normalizes inputs by parsing hex strings into `Transaction` objects without signature checks and derives identities via `transaction.hash()` for both strings and objects.
    - It continues to remove recursive duplicate-input conflicts and prunes pending entries and spent outputs, eliminating mixed hashing paths.

  - **`check_block:`**
    - This function now validates PoW first and fails fast, then enforces the previous-hash link when a tip exists; it requires a strictly increasing timestamp and rejects timestamps more than **+120 seconds** in the future.
    - It checks both header/content length and aggregate regular transaction size against the maximum; it defines the working set as regular transactions for all subsequent checks.
    - It detects in-block duplicate inputs via set comparison, requires exact set equality between requested inputs and available UTXOs, verifies each regular transaction with `check_double_spend=False`, and computes a single deterministic Merkle root with no historical special cases.

  - **`create_block:`**
    - This function now derives mining context via `calculate_difficulty()` and validates the candidate with `check_block` before any writes; it computes fees over regular transactions only and derives the block reward from the updated schedule.
    - It persists the block using the encoded header difficulty extracted from the content; the entire commit (block, coinbase, regular transactions, UTXO updates) runs in a grouped try/except. On failure it deletes the current block row; on success it removes accepted pending transactions and consumed outputs and resets the cached difficulty.

---

### Removed / Replaced Functions:
  - **`difficulty_to_hashrate_old:`**
    - This function has been removed along with its height-gated usage.

  - **`hashrate_to_difficulty_old:`**
    - This function has been removed in favor of the single canonical converter.

  - **`hashrate_to_difficulty_wrong:`**
    - This function has been removed; no callers remain.

  - **`get_transactions_merkle_tree_ordered:`**
    - This function has been removed; Merkle computation no longer depends on block height or transaction insertion order.

  - **`__check:`**
    - This function has been removed; it is not part of the runtime consensus or validation path.

  - **`get_block_reward`:**
    - This function has been replaced with a power-of-two halving policy: initial reward is `64`, the halving interval is `262,144` blocks, and the subsidy ends after **64** halvings (returns `0` thereafter).

---

### Constants:
  - **`BLOCKS_COUNT:`** This constant has been replaced with `BLOCKS_PER_ADJUSTMENT = 512` (integer), aligning the retarget window with a power-of-two period and removing the prior `Decimal(500)` value.
  - **`MAX_SUPPLY:`** This constant has been removed. Emission policy now relies solely on the new halving schedule.
  - **`OLD_BLOCKS_TRANSACTIONS_ORDER:`** This constant has been removed. Historical transaction-order exceptions are no longer supported.

---

### Other Changes:
  - Set `MAX_SUPPLY` in `constants.py` to 33_554_432