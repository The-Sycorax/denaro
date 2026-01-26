"""
Denaro Blockchain Constants

This module consolidates all global constants and environment configuration
used throughout the codebase. Constants are organized by category for easy
reference and maintenance.
"""
import ast
import re
from decimal import Decimal
from dotenv import dotenv_values
from fastecdsa import curve

# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================
# Load environment variables once at module import
_config = dotenv_values(".env")

NODE_DEFAULTS = {
    'POSTGRES_USER':                   'denaro',
    'POSTGRES_PASSWORD':               'denaro',
    'DENARO_DATABASE_NAME':            'denaro',
    'DENARO_DATABASE_HOST':            '127.0.0.1',
    'DENARO_NODE_HOST':                '127.0.0.1',
    'DENARO_NODE_PORT':                '3007',
    'DENARO_SELF_URL':                 '',
    'DENARO_BOOTSTRAP_NODE':           'http://node.denaro.network',
}

LOGGER_DEFAULTS = {    
    'LOG_LEVEL':                       'INFO',
    'LOG_FORMAT':                      '%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    'LOG_DATE_FORMAT':                 '%Y-%m-%dT%H:%M:%S',
    'LOG_CONSOLE_HIGHLIGHTING':        'True',
    'LOG_INCLUDE_RESPONSE_CONTENT':    'False',
    'LOG_INCLUDE_REQUEST_CONTENT':     'False',
    'LOG_INCLUDE_BLOCK_SYNC_MESSAGES': 'False',
}

LOG_MAX_FILE_SIZE = 10 * 1024 * 1024 # 10MB
LOG_MAX_PATH_LENGTH = 320  # Maximum URL path length to log (truncates longer paths)
LOG_BACKUP_COUNT = 5


# WARNING: SOME OF THE VALUES BELOW ARE NOT MEANT TO BE CHANGED! ONLY CHANGE THESE VALUES IF YOU KNOW
# WHAT YOU ARE DOING, CREATING A NEW BLOCKCHAIN, OR FOR TESTING PURPOSES. DOING SO WHILE CONNECTED TO
# THE DENARO MAINNET MAY RESULT IN THIS NODE HAVING AN INVALID BLOCKCHAIN OR OTHER CONSENSUS PROTOCOL
# VIOLATIONS, WHICH MAINNET NODES WILL SWIFTLY REJECT.

# ==================================================================================
# CORE PROTOCOL CONSTANTS
# ==================================================================================
NODE_VERSION = '2.0.2'
ENDIAN = 'little'
CURVE = curve.P256
SMALLEST = 1000000  # Smallest unit divisor


# ==================================================================================
# BLOCKCHAIN PARAMETERS
# ==================================================================================
START_DIFFICULTY = Decimal('6.0')
BLOCK_TIME = 180  # 180 seconds / 3 minute block time
BLOCKS_PER_ADJUSTMENT = 512  # Difficulty adjustment every 512 blocks
MAX_SUPPLY = 33_554_432  # Maximum coin supply (2^25)
MAX_BLOCK_SIZE_HEX = 4096 * 1024  # 4MB in HEX format, 2MB in raw bytes


# ==================================================================================
# MONETARY POLICY PARAMETERS
# ==================================================================================
INITIAL_REWARD = Decimal(64)  # 2^6 DNR
HALVING_INTERVAL = 262144  # 2^18 blocks (targets ~2.5 years)
MAX_HALVINGS = 64  # 64 halvings


# ==================================================================================
# CHECKPOINT AND REORGANIZATION CONSTANTS
# ==================================================================================
# Maximum reorganization depth allowed.
# Prevents deep chain reorganizations that could rewrite established history.
MAX_REORG_DEPTH = 128


# ==================================================================================
# NETWORK AND SYNC CONSTANTS
# ==================================================================================
# Maximum number of blocks that can be submitted in a single request
MAX_BLOCKS_PER_SUBMISSION = 512

# Maximum block content size (same as MAX_BLOCK_SIZE_HEX for validation)
MAX_BLOCK_CONTENT_SIZE = 4_194_304  # 4MB in HEX format

# Peer management
MAX_PEERS = 64  # Maximum number of peers to maintain active connections with
MAX_PEERS_COUNT = 256  # Maximum number of peers to store in the peers database

# Sync operations
MAX_CONCURRENT_SYNCS = 1  # Maximum concurrent sync operations
MAX_BATCH_BYTES = 20 * 1024 * 1024  # 20MB max batch size for push sync

# Transaction limits
MAX_TX_FETCH_LIMIT = 512  # Maximum transactions to fetch in a single request
MAX_MEMPOOL_SIZE = 8192  # Maximum transactions in mempool

# Network timeouts
CONNECTION_TIMEOUT = 10.0  # 10 seconds

# Peer activity tracking
ACTIVE_NODES_DELTA = 60 * 60 * 24 * 7  # peers inactive longer than 7 days are considered stale


# ==================================================================================
# MINING CONSTANTS
# ==================================================================================
# Maximum transaction candidates to consider for block template
MAX_MINING_CANDIDATES = 5000

# Maximum total transaction data size in block template (hex length approximation)
MAX_TX_DATA_SIZE = 1_900_000


# ==================================================================================
# VALIDATION PATTERNS
# ==================================================================================
# Regex pattern for validating hexadecimal strings
VALID_HEX_PATTERN = re.compile(r'^[0-9a-fA-F]+$')

# Regex pattern for validating Denaro addresses (starts with D or E, 45 chars total)
VALID_ADDRESS_PATTERN = re.compile(r'^[DE][1-9A-HJ-NP-Za-km-z]{44}$')


# ==================================================================================
# CONFIGURATION WRAPPERS
# ==================================================================================
class ConfigString(str):
    """
    String subclass that stores a default value.
    """
    def __new__(cls, value, default):
        obj = str.__new__(cls, value)
        obj._default = default
        return obj

    def default(self):
        return self._default

class ConfigBool(int):
    """
    Int subclass acting as a boolean that stores a default value.
    """
    def __new__(cls, value, default):
        obj = int.__new__(cls, bool(value))
        obj._default = default
        return obj

    def default(self):
        return self._default

    def __repr__(self):
        return str(bool(self))

    def __str__(self):
        return str(bool(self))

    def __eq__(self, other):
        return bool(self) == other


# ==================================================================================
# DYNAMIC CONFIGURATION LOADING
# ==================================================================================
DEFAULTS = NODE_DEFAULTS | LOGGER_DEFAULTS 
namespace = globals()

def parse_bool(v):
    """
    Convert "True"/"False" (any casing, with surrounding whitespace) into bool.
    Avoids exceptions by only calling ast.literal_eval for known literals.
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return v
    # Case-insensitive membership check
    if s.casefold() in {"true", "false"}:
        # ast.literal_eval expects "True"/"False"
        return ast.literal_eval(s.title())
    return v

for key, default_raw in DEFAULTS.items():
    # dotenv_values returns strings or None. None is treated as missing.
    raw = _config.get(key)
    value_raw = default_raw if raw is None else raw

    # Parses only boolean-literals. Leaves other values untouched.
    value = parse_bool(value_raw)
    default_val = parse_bool(default_raw)

    # Wraps based on parsed value type.
    if isinstance(value, bool):
        namespace[key] = ConfigBool(value, default_val)
    else:
        # Preserves the original raw string for ConfigString storage.
        namespace[key] = ConfigString(value_raw, default_val)