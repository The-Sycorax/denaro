**refactor(constants): consolidate environment configuration and variable management**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [0621cbf6246c799bc27b0fda8fdc4adbee79b423](https://github.com/The-Sycorax/denaro/commit/0621cbf6246c799bc27b0fda8fdc4adbee79b423)

**Date**: December 23rd, 2025

---

### Overview:
  - This refactor consolidates all environment configuration variables, and most global variables that were previously scattered throughout the codebase into the `constants.py` module. It now serves as the authoritative source for both immutable protocol parameters and mutable runtime settings, and also establishes the default values of all environment variables.
  
  - Environment configuration variables are now loaded from the `.env` file using the `python-dotenv` library, with their default values now hardcoded via the `NODE_DEFAULTS` and `LOGGER_DEFAULTS` dictionaries. 
  
  - Additionally, a dynamic configuration loading system has been implemented to allow access to the default configuration values. It also ensures that when an environment variable is not set by the user, it's default value is used as fallback.

  - Several changes of the codebase have been made to accomindate this refactor. Additionally, Denaro's version has been incremented from `2.0.0` to `2.0.1`.
   
---

### Constants:

- #### Environment Configuration:
  - **`NODE_DEFAULTS`**: Dictionary for default node configuration values.
    - **`POSTGRES_USER`**: PostgreSQL username (default: `'denaro'`).
    
    - **`POSTGRES_PASSWORD`**: PostgreSQL password (default: `'denaro'`).
    
    - **`DENARO_DATABASE_NAME`**: Database name (default: `'denaro'`).
    
    - **`DENARO_DATABASE_HOST`**: Database host (default: `'127.0.0.1'`).
    
    - **`DENARO_NODE_HOST`**: Denaro node host address (default: `'127.0.0.1'`).
    
    - **`DENARO_NODE_PORT`**: Denaro node port (default: `'3007'`).
    
    - **`DENARO_BOOTSTRAP_NODE`**: Bootstrap node URL (default: `'http://node.denaro.network'`).
    
    - **`DENARO_SELF_URL`**: Public address of the node.
  
  - **`LOGGER_DEFAULTS`**: Dictionary for default logging values.
    - **`LOG_LEVEL`**: Logging verbosity level (default: `'INFO'`).
    
    - **`LOG_FORMAT`**: Log message format (default: `'%(asctime)s - %(levelname)s - %(name)s - %(message)s'`).
    
    - **`LOG_DATE_FORMAT`**: Date format for log timestamps (default: `'%Y-%m-%dT%H:%M:%S'`).
    
    - **`LOG_CONSOLE_HIGHLIGHTING`**: Enables Rich console highlighting (default: `True`).
    
    - **`LOG_INCLUDE_REQUEST_CONTENT`**: Includes HTTP request body in logs (default: `False`).
    
    - **`LOG_INCLUDE_RESPONSE_CONTENT`**: Includes HTTP response body in logs (default: `False`).
    
    - **`LOG_INCLUDE_BLOCK_SYNC_MESSAGES`**: Includes verbose block sync logs (default: `False`).

  - **`LOG_MAX_FILE_SIZE`**: Maximum log file size before rotation (10MB).
  
  - **`LOG_MAX_PATH_LENGTH`**: Maximum URL path length to log (320 chars).
  
  - **`LOG_BACKUP_COUNT`**: Number of backup log files to keep (5).

- #### Core Protocol Constants: 
  - **`NODE_VERSION`**: Current node version (incremented from `2.0.0` to `2.0.1`).
  
  - **`ENDIAN`**: Byte order for serialization (`'little'`).
  
  - **`CURVE`**: Elliptic curve for cryptography (`curve.P256`).
  
  - **`SMALLEST`**: Smallest unit divisor (`1000000`).
  
  - **`START_DIFFICULTY`**: Initial difficulty (`Decimal('6.0')`).
  
  - **`BLOCK_TIME`**: Target block time in seconds (`180`).
  
  - **`BLOCKS_PER_ADJUSTMENT`**: Blocks between difficulty adjustments (`512`).
  
  - **`MAX_SUPPLY`**: Maximum coin supply (`33_554_432`).
  
  - **`MAX_BLOCK_SIZE_HEX`**: Maximum block size in hex format (`4096 * 1024` = 4MB).
  
  - **`INITIAL_REWARD`**: Initial block reward (`Decimal(64)`).
  
  - **`HALVING_INTERVAL`**: Blocks between halvings (`262144`).
  
  - **`MAX_HALVINGS`**: Maximum number of halvings (`64`).

- #### Network and Sync Constants:
  - **`MAX_REORG_DEPTH`**: Maximum reorganization depth allowed (`128` blocks).
  
  - **`MAX_BLOCKS_PER_SUBMISSION`**: Maximum blocks in a single submission (`512`).
  
  - **`MAX_BLOCK_CONTENT_SIZE`**: Maximum block content size (`4_194_304` = 4MB).
  
  - **`MAX_PEERS`**: Maximum active peer connections (`64`).
  
  - **`MAX_PEERS_COUNT`**: Maximum peers in database (`256`).
  
  - **`MAX_CONCURRENT_SYNCS`**: Maximum concurrent sync operations (`1`).
  
  - **`MAX_BATCH_BYTES`**: Maximum batch size for push sync (`20 * 1024 * 1024` = 20MB).
  
  - **`MAX_TX_FETCH_LIMIT`**: Maximum transactions to fetch at once (`512`).
  
  - **`MAX_MEMPOOL_SIZE`**: Maximum transactions in mempool (`8192`).
  
  - **`CONNECTION_TIMEOUT`**: Network connection timeout (`10.0` seconds).
  
  - **`ACTIVE_NODES_DELTA`**: Inactivity threshold for peers (`60 * 60 * 24 * 7` = 7 days).

- #### Mining Constants:
  - **`MAX_MINING_CANDIDATES`**: Maximum transactions for block template (`5000`).
  
  - **`MAX_TX_DATA_SIZE`**: Maximum transaction data size in block template (`1_900_000` = 1.9MB).

- #### Validation Patterns:
  - **`VALID_HEX_PATTERN`**: Regex for hex strings (`r'^[0-9a-fA-F]+$'`).
  
  - **`VALID_ADDRESS_PATTERN`**: Regex for Denaro addresses (`r'^[DE][1-9A-HJ-NP-Za-km-z]{44}$'`).

---

### Dynamic Configuration Loading:

  - The default environment configuration values are structured into the `NODE_DEFAULTS` and `LOGGER_DEFAULTS` dictionaries. These are then merged into a unified `DEFAULTS` dictionary that acts as the baseline configuration for the loading process. 

  - The system iterates over the default values, matching against user-provided values in the `.env` file. If an environment variable is not set in the `.env` file, then the default value for it is used. The values are then processed through the `parse_bool` function, which utilizes `ast.literal_eval` and case-insensitive matching to correctly interpret boolean strings.

  - Finally, the values are injected into the module's global namespace using `ConfigString` and `ConfigBool` wrapper classes. These classes inherit directly from Python's native `str` and `int` types to ensure full compatibility with existing code, while simultaneously preserving the original default value for reference.

  - ### Classes:
    - **`ConfigString`**:
      - String subclass that stores both the runtime value and its default value. 
      - Implements `default()` method to access the default value.
    
    - **`ConfigBool`**:
      - Int subclass acting as a boolean that stores both the runtime value and its default value. 
      - Overrides `__repr__`, `__str__`, and `__eq__` to behave like a boolean.
      - Implements `default()` method to access the default value.

  - ### Functions:
    - **`parse_bool`**:
      - This function converts the string representations of "True"/"False" into Python booleans. 
      - Uses `ast.literal_eval` only for known boolean literals and returns the original value unchanged if it's not a recognizable boolean string.

---

### Other Changes:

  - The module now includes comprehensive docstrings explaining its purpose.
  
  - A warning comment has been added regarding the implications of changing any constant that is central to Denaro's underlying protocol and codebase.
  
  - Constants are now organized into clear commented sections for better readability and maintainability.
  
  - Various internal variables throughout the codebase have been replaced with imports from `constants.py`. Some variables have also been renamed.
    
  - The default value for `DENARO_BOOTSTRAP_NODE` has been updated to `http://node.denaro.network`. Nodes will now attempt to connect to this node if one is not set.

  - This refactor is closely related to the logging refactor (`2025-12-12-refactor(logging).md`), as logging configuration constants are defined here and used by the logging system.

