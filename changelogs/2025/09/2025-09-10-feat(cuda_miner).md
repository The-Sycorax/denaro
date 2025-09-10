**feat(cuda_miner): add a single-worker CUDA miner that uses PyCUDA**

**Contributer**: The-Sycorax (https://github.com/The-Sycorax)

**Date**: September 10th, 2025

---

### Overview
- This commit introduces a GPU accelerated single worker miner that uses PyCUDA to JIT (Just in Time) compile a CUDA kernel for a SHA256 based nonce search over a constant block prefix.
- Implements integer and fractional difficulty. Integer difficulty requires a suffix match on the previous block hash in hex. Fractional difficulty restricts the next hex digit to a limited uppercase charset.
- Sends requests to a Denaro node API via HTTP endpoints `/get_mining_info` and `/push_block`. Handles success, stale, and failure statuses.
- Uses a 90 second refresh window for work to reduce stale results. Performs batched searches with grid step based nonce progression.
- Provides a configurable CLI and GPU launch parameters.
- Prepares `PATH` and `LD_LIBRARY_PATH` entries for CUDA toolchain availability. 
- This commit also introduces detailed documentation to properly setup, use, and fine tune the CUDA miner.

---

### Command Line Arguments:
- `--address`, `-a`: Mining address to receive rewards *(required)*.
- `--node`, `-n`: URL of the Denaro node API (Default: http://127.0.0.1:3006/).
- `--max-blocks`, `-m`: Max number of blocks to mine before exit (Default: 10).
- `--gpu-blocks`: CUDA grid blocks per launch (Default: 256).
- `--gpu-threads`: CUDA threads per block (Default: 256).
- `--gpu-iterations`: Iterations per thread per kernel batch (Default: 10000).
- `--gpu-arch`: Sets the `nvcc` architecture flag *(required)*.

---

### Functions:
- **`build_prefix`**:
  - Constructs the constant preimage prefix that excludes the 4 byte nonce.  
  - Layout is previous `block hash` bytes, `miner address` bytes, `merkle root` bytes, c`urrent UTC timestamp` as 4 byte little endian, and `scaled difficulty` as 2 byte little endian where `scale = int(difficulty * 10)`.  
  - If the address length is 33 bytes, prepends a single byte value 0x02 for compatibility with the CPU miner.  
  - Returns: bytes containing the prefix.

- **`compute_fractional_charset`**:
  - Splits difficulty into integer part idiff and fractional part.  
  - When the fractional part is greater than zero, limits the allowed hexadecimal set for the next hex digit to the first `ceil(16 * (1 - fractional))` characters of 0123456789ABCDEF.  
  - Returns: tuple of (int idiff, str allowed uppercase charset).

- **`make_last_block_chunk`**:
  - Produces the uppercase hex suffix of the previous block hash with length equal to idiff.  
  - Used for hex digit prefix matching on the device.  
  - Returns: str containing the uppercase suffix.

- **`string_to_bytes`**:
  - Decodes the provided mining address from hex, falling back to Base58 when hex parsing fails.  
  - Returns: bytes for inclusion in the block prefix.

- **`timestamp`**:
  - Computes the current UTC POSIX timestamp.  
  - Returns: int seconds since epoch.

- **`submit_block`**:
  - Sends a post request to a Denaro node API at the `/push_block` endpoint. 
  - The post request includes the following JSON payload fields: `block_content` as hex, `txs` as provided by the node, and `id` equal to `last_block_id` + 1.  
  - Maps all response messages from the node to their associated constants accordingly.
  - Returns: int `STATUS_*` code.

- **`prepend_env_path_if_not_set`**  
  - Prepends the given path to the named environment variable only when they are not already present.
  - Supports `PATH` and `LD_LIBRARY_PATH` preparation for CUDA discovery.  

- **`main`**  
  - Parses CLI arguments and prepares `PATH` and `LD_LIBRARY_PATH`.  
  - Builds the CUDA module, copies SHA256 round constants into device constant memory, and enters the mining loop.  
  - Repeatedly fetches `get_mining_info` with retry, builds inputs, launches the kernel in batches within the refresh window, submits a found block, and manages success, stale or failed outcomes.  

---

### CUDA device functions and kernel:
- **`sha256_transform`**:
  - Performs the SHA256 compression on a 512 bit chunk using round constants in device constant memory.  

- **`sha256_init`**:
  - Initializes the SHA256 state to the standard initial vector.  

- **`sha256_update`**:
  - Absorbs bytes into a 64 byte buffer and runs the compression function on full blocks.  

- **`sha256_final`**:
  - Pads the message, appends the bit length, performs the final compression, and writes the 32 byte digest.  

- **`sha256_to_hex_uc`**:
  - Converts a 32 byte digest to a 64 character uppercase hex string.  

- **`nibble_prefix_match`**:
  - Compares the first idiff hex characters of a computed digest to a required suffix of the previous block hash.  
  - Returns: bool.

- **`bytes_contains_uc`**:
  - Tests membership of a single uppercase hex character within the allowed charset.  
  - Returns: bool.

- **`miner_kernel`**:
  - Performs the nonce search over `SHA256(prefix || nonce)` with grid step based progression and `iters_per_thread` inner loops.  
  - Publishes the first found nonce to a single uint32 result using `atomicCAS` and enables early exit for other threads.  

---

### Constants:
- **`WORKER_REFRESH_SECONDS`**:
  - Duration in seconds for each mining batch window before refreshing mining information from the node.

- **`DEFAULT_NODE_URL`**:
  - Default base URL for the node HTTP API used by the miner.

- **`STATUS_PENDING`**:
  - Status code for a pending or unknown submission state.

- **`STATUS_SUCCESS`**:
  - Status code returned when the node accepts the submitted block.

- **`STATUS_STALE`**:
  - Status code returned when the submitted block is stale because another block was accepted first.

- **`STATUS_FAILED`**:
  - Status code returned when submission fails due to rejection or network error.

- **`CUDA_SOURCE`**:
  - CUDA C source for SHA256 helpers and the miner kernel. Compiled at runtime by PyCUDA.

- **`K_CONST_HOST`**:
  - Host side array of 64 SHA256 round constants copied to device constant memory.