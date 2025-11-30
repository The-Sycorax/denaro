

**refactor(consensus): update consensus rules and implement consensus engine**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [6eac89994593d57186b715aa235f2031ca9c6c07](https://github.com/The-Sycorax/denaro/commit/6eac89994593d57186b715aa235f2031ca9c6c07)

**Date**: 2025-10-24

---

## Overview:

**This commit introduces a comprehensive, versioned consensus engine to formalize and manage the blockchain's protocol rules. This refactor decouples consensus logic from block management, and creates a scalable framework for future network upgrades and hard/soft forks.**

At the core of this refactor is the new `denaro/consensus.py` module, which defines consensus versions, activation schedules, and rule interfaces. 

Additionally a `Consensus_V1` class has been introduced, and includes critical security and stability improvements: a proper binary Merkle tree implementation, Median Time Past (MTP) timestamp validation, a more stable difficulty adjustment algorithm, and stricter coinbase transaction rules.

The `denaro/manager.py` module has been significantly refactored to utilize the new consensus engine, delegating all consensus-critical validation to the appropriate versioned ruleset based on block height.

---

- ### New `denaro/consensus.py`:
    - ### Overview:
        - This new module establishes a robust, versioned consensus engine designed to formalize protocol rules and manage network upgrades in a clean, scalable manner. The architecture decouples rule definitions from their enforcement, making future hard or soft forks easier to implement and reason about.
        
        - The system is built on several core components:
            - **`ConsensusSchedule`**: Acts as the single source of truth for all network upgrades, defining activation heights for each consensus version.
            
            - **`BaseConsensusRules`**: An abstract base class that defines the common interface for all consensus rule sets, ensuring every version implements the same validation methods.
            
            - **`Consensus_V1`**: The first concrete implementation of the consensus rules. It introduces critical security and stability fixes such as a proper binary Merkle tree, Median Time Past (MTP) timestamp validation, and an improved difficulty adjustment algorithm.
            
            - **`ConsensusEngine`**: A central factory that, given a block height, consults the schedule and returns the appropriate (and cached) ruleset object for validation.

        ---
        
    - **`ConsensusVersion` Class**:
        - This `IntEnum` class was added to provide clear, named identifiers for different sets of consensus rules (e.g., `CONSENSUS_V1`).

        ---

    - **`ConsensusActivation` Class**:
        - **`__init__`**:
            - Initializes a new `dataclass` to define a specific consensus upgrade, containing its version, activation height, description, and fork status.

        - **`is_active`**:
            - A utility method to check if the consensus version is active by comparing a given block height against the `activation_height`. Returns `True` if `block_height >= activation_height`.

        ---

    - **`ConsensusSchedule` Class**:
        - **`__init__`**:
            - Initializes the schedule and calls `_initialize_schedule` to populate it with the chronologically ordered list of defined consensus activations.

        - **`_initialize_schedule`**:
            - Defines the single source of truth for the consensus upgrade schedule, populating the `_activations` list. Includes validation to ensure activation heights are strictly chronological to prevent configuration errors.

        - **`get_active_version`**:
            - Determines which `ConsensusVersion` is active for a specific block height. It iterates through the ordered activations and returns the last one whose height has been met, ensuring the most up-to-date rules are selected.

        - **`get_activation_height`**:
            - Retrieves the exact activation block height for a given `ConsensusVersion`, allowing other parts of the system to query for specific fork heights.

        - **`is_hard_fork`**:
            - Checks if a specific consensus version was introduced via a hard fork by looking up its `is_hard_fork` flag in the schedule.

        - **`get_all_activations`**:
            - Returns a copy of all scheduled consensus activations.
        ---

    - **`BaseConsensusRules` Class**:
        - **`version`**:
            - Abstract property that must be implemented by subclasses to identify which `ConsensusVersion` they represent.

        - **`calculate_merkle_tree`**:
            - Abstract method defining the interface for Merkle tree calculation. Each consensus version must provide its own implementation.

        - **`validate_timestamp`**:
            - Abstract method defining the interface for block timestamp validation.

        - **`calculate_new_difficulty`**:
            - Abstract method defining the interface for difficulty adjustment calculations.

        - **`validate_coinbase_transactions`**:
            - Abstract method defining the interface for rules governing coinbase transactions within a block's transaction list.

        - **`validate_field_ranges`**:
            - A concrete method that validates block field ranges (`random_value`, `difficulty`) to prevent out-of-range or malformed values. This rule is a soft fork, implemented in the base class so it is shared and enforced by all consensus versions.

        ---

    - **`Consensus_V1` Class**:
        - **`version`**:
            - Implements the abstract property, returning `ConsensusVersion.CONSENSUS_V1`.

        - **`calculate_merkle_tree`**:
            - Implements a proper binary Merkle tree algorithm. It first sorts all transaction hashes lexicographically to ensure the root is deterministic. 
            It then iteratively pairs up adjacent hashes, concatenates them, and hashes the result. 
            
            - If there is an odd number of hashes in a level, the last hash is duplicated to form a pair. This process repeats until only one root hash remains.

        - **`validate_timestamp`**:
            - Implements Median Time Past (MTP) validation. A block is valid only if its timestamp is strictly greater than the median timestamp of the previous 11 blocks. 
            
            - This prevents various timestamp manipulation attacks. It also enforces a stricter future-time limit, rejecting blocks with a timestamp more than 60 seconds ahead of the node's current system time.

        - **`calculate_new_difficulty`**:
            - ~~Implements an improved difficulty adjustment formula. It clamps the calculated time ratio between `0.5x` and `2.0x`  for more stable retargeting. The new difficulty is calculated as `current_difficulty * clamped_ratio` and is floored by `START_DIFFICULTY`.~~
            
            - **Bug Fix ([d1ce09e4](https://github.com/The-Sycorax/denaro/commit/d1ce09e4a4e7cc0d7373674aa49b9ad77ff5098e))**:
                - This implementation was later corrected in commit [d1ce09e4](https://github.com/The-Sycorax/denaro/commit/d1ce09e4a4e7cc0d7373674aa49b9ad77ff5098e) to fix a bug causing exaggerated difficulty adjustments. The corrected logic converts the current difficulty to its equivalent hashrate. The time ratio is then applied to this hashrate value, and the result is converted back to difficulty. Ratio clamping was also reverted back to `0.25x` and `4.0x`.

        - **`validate_coinbase_transactions`**:
            - Implements a strict rule that forbids any `CoinbaseTransaction` from being included in the list of regular transactions submitted with a block. This ensures the coinbase is only generated by the winning miner and is not part of the standard transaction pool.

        ---

    - **`ConsensusEngine` Class**:
        - **`__init__`**:
            - Initializes the engine with the consensus schedule and a map of `ConsensusVersion` enums to their corresponding rule class implementations.

        - **`get_rules`**:
            - A factory method that returns the correct, cached instance of a `BaseConsensusRules` subclass. Given a block height, it determines the active version from the schedule and returns the singleton instance for that version. It also uses an internal `_rules_cache` to avoid re-instantiating rule objects for every block validation.

        - **`is_hard_fork_active`**:
            - Checks if a specific hard fork version is active at a given block height.

        ---

    - **Other Functions**:
        - **`get_consensus_info`**:
            - A utility function that provides a dictionary of all consensus activations, useful for debugging and for peers to exchange compatibility information upon connection.

        - **`get_median_time_past`**:
            - Calculates the median timestamp of the last N blocks (default 11). It queries the database for the timestamps, sorts the resulting list, and returns the median value. This is a crucial component for MTP validation.

        ---

    - **Constants**:
        - **`CONSENSUS_SCHEDULE`**: Added a global instance of the `ConsensusSchedule`.

        - **`CONSENSUS_ENGINE`**: Added a global instance of the `ConsensusEngine`.

---

- ### `denaro/manager.py` Changelog:
    - ### Overview:
        - This module has been significantly refactored to integrate with the new versioned consensus engine. All consensus-critical logic is now delegated to the appropriate ruleset provided by the `ConsensusEngine`. 
        
        - The core `check_block` function as been rewritten to be a robust, self-contained validator that orchestrates these version-aware checks based on block height. 
        
        - A thread-safe `Manager` class has been introduced to handle difficulty caching. Additionally, the transaction clearing logic was updated to use an iterative algorithm to prevent potential stack overflows.

    - **`Manager` Class**:
        - **`get_difficulty_safe`**:
            - A new thread-safe method to retrieve the current difficulty. It uses an `asyncio.Lock` to ensure that if the cache is empty, the expensive `calculate_difficulty` function is called only once, even if multiple coroutines request the difficulty at the same time.

        - **`invalidate_difficulty`**:
            - A new thread-safe method to clear the difficulty cache by setting it to `None`. This must be called after a new block is added to ensure that the next difficulty request triggers a recalculation based on the new chain tip.

        ---

    - **Added Functions**:
        - **`get_consensus_version_info`**:
            - A public API function that wraps `consensus.get_consensus_info` to expose consensus schedule information for network coordination and debugging.

        - **`validate_consensus_compatibility`**:
            - A utility for P2P networking to check if a peer's advertised consensus schedule is compatible with the current node by comparing activation versions and heights. This prevents connections to nodes on an incompatible fork.

        ---

    - **Modified Functions**:
        - **`calculate_difficulty`**:
            - Refactored to be consensus-aware. It now fetches the appropriate consensus ruleset for the current block height via `CONSENSUS_ENGINE.get_rules`. After calculating the time elapsed and adjustment ratio, it delegates the final difficulty calculation to `rules.calculate_new_difficulty`, passing the necessary parameters.
            
            - Logging was improved to show which consensus version is being used for the adjustment, aiding in debugging fork-related issues.

        - **`get_difficulty`**:
            - Modified to use the new thread-safe `Manager.get_difficulty_safe` method for all public difficulty retrievals.

        - **`check_block_is_valid`**:
            - The method's role was narrowed to be a pure Proof-of-Work check. It validates if a block's hash meets the target specified by the `difficulty` and `last_block` hash. It correctly handles both integer difficulties (prefix matching) and fractional difficulties.

        - **`clear_pending_transactions`**:
            - Rewritten from a recursive algorithm to an iterative algorithm. This funtction is meant to handle large numbers of conflicting transactions in the mempool without risking a `RecursionError`. Additionally, it simplifies the control flow for removing double-spends and invalid transactions.

        - **`get_transactions_merkle_tree`**:
            - Refactored to be consensus-aware, replacing the old implementation. The function now serves as a high-level wrapper that accepts a `block_height` and a list of transactions. It uses the `CONSENSUS_ENGINE` to fetch the correct ruleset for that block height and then delegates the Merkle root calculation to that object's `calculate_merkle_tree` method.

        
        - **`check_block`**:
            - **This function has been completely rewritten to be the primary, self-contained validation function that orchestrates all checks using the consensus engine. Its validation process is now stateless and comprehensive:**
                
                - It fetches the correct ruleset for the block's height via the `CONSENSUS_ENGINE.get_rules` method, which is then used to orchestrate all version-specific checks and to apply shared soft-fork rules.
                
                - It also performs a critical security check by independently calculating the `expected_difficulty` based on the predecessor's chain state. This ensures a block's difficulty is validated against the chain history, not the node's potentially stale cached state.
                
                - Additionally, it delegates all core consensus logic to the selected ruleset, including Median Time Past (MTP) timestamp validation (`validate_timestamp`), version-specific Merkle root calculation (`calculate_merkle_tree`), and coinbase transaction rules (`validate_coinbase_transactions`).

        - **`create_block`**:
            - Updated to orchestrate the creation and validation of a new block. It now relies entirely on the improved version-aware `check_block` function for all validation. 
            
            - After a block is successfully validated and committed to the database, this function calls `Manager.invalidate_difficulty` to clear the cache.

        ---

    - **Constants**:
        - **`BLOCK_TIME`**: Removed and moved to `denaro/constants.py`.

        - **`BLOCKS_PER_ADJUSTMENT`**: Removed and moved to `denaro/constants.py`.

        - **`START_DIFFICULTY`**: Removed and moved to `denaro/constants.py`.

---

- ### `denaro/node/main.py` Changelog:
    - **Modified Functions**:
        - **`root`**:
            - Updated to return `node_version` instead of the old `version` key, reflecting the change in `constants.py`.

        - **`get_mining_info`**:
            - Modified to calculate the `next_block_height` for the block template being constructed.
            - The call to `get_transactions_merkle_tree` now passes this `next_block_height` to ensure the Merkle root for the new block is calculated using the consensus rules that will be active at that height.

        - **`chain_tx`**:
            - Similar to `get_mining_info`, the call to `get_transactions_merkle_tree` was updated to pass the `next_block_height`, ensuring consensus-aware Merkle root generation for mining templates.

---

- ### `denaro/constants.py` Changelog:
    - **`NODE_VERSION`**: Replaces the old `VERSION` constant.

    - **`START_DIFFICULTY`**: Moved from `manager.py` to centralize canonical chain parameters.

    - **`BLOCK_TIME`**: Moved from `manager.py`.

    - **`BLOCKS_PER_ADJUSTMENT`**: Moved from `manager.py`.