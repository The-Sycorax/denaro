from fastecdsa import curve
from decimal import Decimal

NODE_VERSION = '2.0.0'

ENDIAN = 'little'
CURVE = curve.P256

SMALLEST = 1000000

# --- Canonical Chain Parameters ---
START_DIFFICULTY = Decimal('6.0')
BLOCK_TIME = 180 # 180 Seconds/3 minute block time
BLOCKS_PER_ADJUSTMENT = 512 # Difficulty adjustment every 512 blocks
MAX_SUPPLY = 33_554_432
MAX_BLOCK_SIZE_HEX = 4096 * 1024  # 4MB in HEX format, 2MB in raw bytes
