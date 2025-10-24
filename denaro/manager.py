# denaro/manager.py
"""
Block management with versioned consensus rules.

This module handles block creation, validation, and difficulty adjustment
using the consensus engine for rule versioning.
"""

import hashlib
from decimal import Decimal
from io import BytesIO
from math import ceil, floor, log
from typing import Tuple, List, Union
import asyncio

from . import Database
from .constants import ENDIAN, MAX_BLOCK_SIZE_HEX, START_DIFFICULTY, BLOCK_TIME, BLOCKS_PER_ADJUSTMENT
from .helpers import sha256, timestamp, bytes_to_string, string_to_bytes
from .transactions import CoinbaseTransaction, Transaction

from .consensus import (
    CONSENSUS_ENGINE,
    ConsensusVersion,
    get_median_time_past,
    get_consensus_info
)


# ============================================================================
# LEGACY DIFFICULTY FUNCTIONS (for Genesis consensus)
# ============================================================================

def difficulty_to_hashrate(difficulty: Decimal) -> int:
    """Legacy function for Genesis consensus blocks."""
    decimal_part = difficulty % 1
    integer_part = floor(difficulty)
    return Decimal(16 ** integer_part) * (
        Decimal(16) / ceil(Decimal(16) * (Decimal(1) - decimal_part))
    )


def hashrate_to_difficulty(hashrate: int) -> Decimal:
    """Legacy function for Genesis consensus blocks."""
    if hashrate <= 0:
        return START_DIFFICULTY

    integer_part = floor(log(hashrate, 16))
    ratio = hashrate / (16 ** integer_part)

    for i in range(10):
        decimal_step = Decimal(i) / 10
        coeff = Decimal(16) / ceil(Decimal(16) * (Decimal(1) - decimal_step))
        if coeff >= ratio:
            return Decimal(integer_part) + decimal_step

    return Decimal(integer_part) + Decimal('0.9')


# ============================================================================
# DIFFICULTY CALCULATION
# ============================================================================

async def calculate_difficulty() -> Tuple[Decimal, dict]:
    """
    Calculate difficulty using version-appropriate consensus rules.
    """
    database = Database.instance
    last_block = await database.get_last_block()

    if last_block is None:
        return START_DIFFICULTY, {}

    last_block = dict(last_block)
    block_id = last_block['id']
    
    if block_id < BLOCKS_PER_ADJUSTMENT:
        return START_DIFFICULTY, last_block

    if block_id % BLOCKS_PER_ADJUSTMENT != 0:
        return last_block['difficulty'], last_block

    # Get consensus rules for this block height
    rules = CONSENSUS_ENGINE.get_rules(block_id)
    
    first_block_of_period = await database.get_block_by_id(
        block_id - BLOCKS_PER_ADJUSTMENT + 1
    )
    
    time_elapsed = last_block['timestamp'] - first_block_of_period['timestamp']
    time_elapsed = max(1, time_elapsed)  # Prevent division by zero
        
    avg_block_time = time_elapsed / BLOCKS_PER_ADJUSTMENT
    ratio = Decimal(BLOCK_TIME) / Decimal(avg_block_time)
    
    # Calculate new difficulty using version-specific rules
    new_difficulty = rules.calculate_new_difficulty(
        time_ratio=ratio,
        current_difficulty=last_block['difficulty'],
        legacy_hashrate_func=difficulty_to_hashrate
    )
    
    print(f"\nDifficulty Adjustment at block {block_id} "
          f"(Consensus: {rules.version.name}):")
    print(f"  Time Elapsed: {time_elapsed}s for {BLOCKS_PER_ADJUSTMENT} blocks")
    print(f"  Average Block Time: {avg_block_time:.2f}s (Target: {BLOCK_TIME}s)")
    print(f"  Adjustment Ratio: {ratio:.4f}")
    print(f"  Old Difficulty: {last_block['difficulty']} -> "
          f"New Difficulty: {new_difficulty}\n")

    return new_difficulty, last_block


# ============================================================================
# MANAGER CLASS (Thread-safe difficulty caching)
# ============================================================================

class Manager:
    """Manages blockchain state with thread-safe operations."""
    
    difficulty: Tuple[float, dict] = None
    _difficulty_lock = asyncio.Lock()
    
    @classmethod
    async def get_difficulty_safe(cls) -> Tuple[Decimal, dict]:
        """Thread-safe difficulty retrieval."""
        async with cls._difficulty_lock:
            if cls.difficulty is None:
                cls.difficulty = await calculate_difficulty()
            return cls.difficulty
    
    @classmethod
    async def invalidate_difficulty(cls):
        """Thread-safe difficulty invalidation."""
        async with cls._difficulty_lock:
            cls.difficulty = None


async def get_difficulty() -> Tuple[Decimal, dict]:
    """Public API for getting current difficulty."""
    return await Manager.get_difficulty_safe()


# ============================================================================
# PROOF OF WORK VALIDATION
# ============================================================================

async def check_block_is_valid(block_content: str, difficulty: Decimal, last_block: dict) -> bool:
    """Validate block meets proof-of-work requirements against a specific last_block."""
    block_hash = sha256(block_content)

    if not last_block:  # Genesis block case
        return True

    last_block_hash = last_block['hash']
    decimal = difficulty % 1
    difficulty_int = floor(difficulty)
    
    if decimal > 0:
        charset = '0123456789abcdef'
        count = ceil(16 * (1 - decimal))
        return (
            block_hash.startswith(last_block_hash[-difficulty_int:]) and 
            block_hash[difficulty_int] in charset[:count]
        )
    
    return block_hash.startswith(last_block_hash[-difficulty_int:])


# ============================================================================
# BLOCK REWARD
# ============================================================================

def get_block_reward(block_number: int) -> Decimal:
    """
    Calculates the block reward based on a Bitcoin-style halving schedule.
    This monetary policy is chosen for its optimal balance of a scarce total
    supply, frequent halving events, strong initial security, and mathematical
    elegance, with all parameters being powers of two.

    - Initial Reward: 64 (2^6) DEN
    - Halving Interval: 262,144 (2^18) blocks (targets ~2.5 years)
    - Total Supply: 33,554,432 (2^25) DEN
    - Emission Lifespan: ~160 years (64 halvings)
    """
    # --- Canonical Monetary Policy Parameters ---
    INITIAL_REWARD = Decimal(64)
    HALVING_INTERVAL = 262144
    MAX_HALVINGS = 64

    # The first block is #1. We use (block_number - 1) to ensure the first
    # halving occurs precisely at block 262,144.
    if block_number <= 0:
        return Decimal(0)
    
    # Determine how many halving events have occurred.
    halvings = floor((block_number - 1) / HALVING_INTERVAL)

    # After the maximum number of halvings, the subsidy ends permanently.
    if halvings >= MAX_HALVINGS:
        return Decimal(0)
        
    # Calculate the reward for the current period.
    # The formula is: initial_reward / (2^halvings)
    block_reward = INITIAL_REWARD / (2 ** halvings)
    
    return block_reward


# ============================================================================
# TRANSACTION UTILITIES
# ============================================================================

def get_transactions_size(transactions: List[Transaction]) -> int:
    """Calculate total size of transactions in hex."""
    return sum(len(transaction.hex()) for transaction in transactions)


async def clear_pending_transactions(transactions=None):
    """
    Clear invalid pending transactions iteratively (non-recursive).
    """
    database: Database = Database.instance
    await database.clear_duplicate_pending_transactions()
    
    transactions = transactions or await database.get_pending_transactions_limit(
        hex_only=True
    )
    
    max_iterations = 100
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        used_inputs = []
        to_remove = []
        
        for transaction in transactions:
            if isinstance(transaction, str):
                transaction = await Transaction.from_hex(
                    transaction, 
                    check_signatures=False
                )
            
            tx_hash = transaction.hash()
            tx_inputs = [
                (tx_input.tx_hash, tx_input.index) 
                for tx_input in transaction.inputs
            ]
            
            if any(used_input in tx_inputs for used_input in used_inputs):
                to_remove.append(tx_hash)
                continue
            
            used_inputs += tx_inputs
        
        if to_remove:
            for tx_hash in to_remove:
                await database.remove_pending_transaction(tx_hash)
                print(f'Removed conflicting transaction: {tx_hash}')
            
            transactions = await database.get_pending_transactions_limit(
                hex_only=True
            )
            continue
        
        unspent_outputs = await database.get_unspent_outputs(used_inputs)
        double_spend_inputs = set(used_inputs) - set(unspent_outputs)
        
        if double_spend_inputs == set(used_inputs):
            await database.remove_pending_transactions()
            break
        elif double_spend_inputs:
            await database.remove_pending_transactions_by_contains([
                tx_input[0] + bytes([tx_input[1]]).hex() 
                for tx_input in double_spend_inputs
            ])
            transactions = await database.get_pending_transactions_limit(
                hex_only=True
            )
            continue
        
        break
    
    if iteration >= max_iterations:
        print(f"Warning: Transaction clearing hit iteration limit")


# ============================================================================
# BLOCK SERIALIZATION
# ============================================================================

def block_to_bytes(last_block_hash: str, block: dict) -> bytes:
    """Convert block dict to bytes for hashing."""
    address_bytes = string_to_bytes(block['address'])
    version = bytes([])
    if len(address_bytes) != 64:
        version = bytes([2])
    
    return (
        version +
        bytes.fromhex(last_block_hash) +
        address_bytes +
        bytes.fromhex(block['merkle_tree']) +
        block['timestamp'].to_bytes(4, byteorder=ENDIAN) +
        int(float(block['difficulty']) * 10).to_bytes(2, ENDIAN) +
        block['random'].to_bytes(4, ENDIAN)
    )


def split_block_content(block_content: str) -> Tuple:
    """Parse block content into components."""
    _bytes = bytes.fromhex(block_content)
    stream = BytesIO(_bytes)
    version = 1 if len(_bytes) == 138 else int.from_bytes(stream.read(1), ENDIAN)

    previous_hash = stream.read(32).hex()
    address = bytes_to_string(stream.read(64 if version == 1 else 33))
    merkle_tree = stream.read(32).hex()
    timestamp_val = int.from_bytes(stream.read(4), ENDIAN)
    difficulty = int.from_bytes(stream.read(2), ENDIAN) / Decimal(10)
    random = int.from_bytes(stream.read(4), ENDIAN)
    
    return previous_hash, address, merkle_tree, timestamp_val, difficulty, random


# ============================================================================
# BLOCK VALIDATION
# ============================================================================

# denaro/manager.py

async def check_block(block_content: str, transactions: List[Transaction], mining_info: tuple = None) -> bool:
    """
    Comprehensive block validation using version-appropriate consensus rules.
    This function is self-contained and recalculates all necessary context
    to ensure blocks are valid regardless of the node's current state.
    """
    # Early size validation (soft fork - all versions)
    if len(block_content) > MAX_BLOCK_SIZE_HEX:
        print(f"Block rejected: content exceeds {MAX_BLOCK_SIZE_HEX} hex chars")
        return False

    # --- PARSE BLOCK CONTENT EARLY ---
    previous_hash, address, merkle_tree, content_time, content_difficulty, random_val = \
        split_block_content(block_content)

    database = Database.instance
    last_block_for_validation = await database.get_block(previous_hash)

    # --- REVISED PREDECESSOR CHECK & BLOCK NUMBER DERIVATION ---
    is_genesis = False
    if last_block_for_validation:
        block_no = last_block_for_validation['id'] + 1
    elif mining_info and not last_block_for_validation:
        # This is a newly mined block and its predecessor is not in the DB.
        # This is ONLY valid if it's the genesis block.
        _, last_block_from_miner_context = mining_info
        if not last_block_from_miner_context: # The context last_block is empty {}
            block_no = 0
            is_genesis = True
        else:
            # Miner submitted a block that doesn't connect to their provided context.
            print(f"Block rejected: Miner submitted block with unknown previous hash '{previous_hash}'")
            return False
    else:
        # A block from sync (no mining_info) whose predecessor is not found is an orphan.
        print(f"Block rejected: Sync block has unknown previous hash '{previous_hash}'")
        return False

    # --- ACCURATE DIFFICULTY CALCULATION ---
    expected_difficulty = START_DIFFICULTY
    # For genesis, we use START_DIFFICULTY. For all others, we calculate.
    if not is_genesis and last_block_for_validation:
        last_block_id = last_block_for_validation['id']
        
        if last_block_id > 0 and last_block_id % BLOCKS_PER_ADJUSTMENT == 0:
            start_period_id = last_block_id - BLOCKS_PER_ADJUSTMENT + 1
            first_block_of_period = await database.get_block_by_id(start_period_id)
            
            if not first_block_of_period:
                 print(f"CRITICAL: Could not find block {start_period_id} for difficulty calc at height {block_no}")
                 return False

            time_elapsed = last_block_for_validation['timestamp'] - first_block_of_period['timestamp']
            time_elapsed = max(1, time_elapsed)
            
            avg_block_time = time_elapsed / (BLOCKS_PER_ADJUSTMENT - 1)
            ratio = Decimal(BLOCK_TIME) / Decimal(avg_block_time)

            rules_for_calc = CONSENSUS_ENGINE.get_rules(last_block_id)
            expected_difficulty = rules_for_calc.calculate_new_difficulty(
                time_ratio=ratio,
                current_difficulty=last_block_for_validation['difficulty'],
                legacy_hashrate_func=difficulty_to_hashrate
            )
        else:
            expected_difficulty = last_block_for_validation['difficulty']

    # --- CONSENSUS CHECK: DIFFICULTY ---
    if content_difficulty != expected_difficulty:
        print(f"Block {block_no} rejected: Difficulty mismatch. "
              f"Expected: {expected_difficulty}, Got: {content_difficulty}")
        return False
        
    rules = CONSENSUS_ENGINE.get_rules(block_no)

    # --- CONSENSUS CHECK: PROOF OF WORK ---
    # For genesis from a miner, the context last_block is {}, so we pass that.
    # For sync, we pass the real last block from the DB.
    pow_context_block = last_block_for_validation
    if is_genesis and mining_info:
        _, pow_context_block = mining_info

    if not await check_block_is_valid(block_content, content_difficulty, pow_context_block):
        print(f"Block {block_no} failed PoW validation")
        return False

    # --- CONSENSUS CHECK: FIELD RANGES (SOFT FORK) ---
    if not rules.validate_field_ranges(random_val, content_difficulty):
        return False

    # --- CONSENSUS CHECK: TIMESTAMP ---
    last_timestamp = last_block_for_validation.get('timestamp', 0) if last_block_for_validation else 0
    current_time = timestamp()
    
    is_valid_timestamp = await rules.validate_timestamp(
        content_time=content_time,
        block_id=block_no,
        last_timestamp=last_timestamp,
        current_time=current_time,
        get_median_time_past_func=lambda bid: get_median_time_past(database, bid)
    )
    if not is_valid_timestamp:
        return False

    # --- TRANSACTION VALIDATION ---
    regular_transactions = [
        tx for tx in transactions 
        if isinstance(tx, Transaction) and not isinstance(tx, CoinbaseTransaction)
    ]
    
    if not rules.validate_coinbase_transactions(regular_transactions):
        return False
    
    if get_transactions_size(regular_transactions) > MAX_BLOCK_SIZE_HEX:
        print(f"Block {block_no} total transaction size too large")
        return False

    if regular_transactions:
        check_inputs = sum([
            [(tx_input.tx_hash, tx_input.index) for tx_input in tx.inputs] 
            for tx in regular_transactions
        ], [])
        
        if len(set(check_inputs)) != len(check_inputs):
            print(f"Block {block_no} contains internal double-spend")
            return False
        
        unspent_outputs = await database.get_unspent_outputs(check_inputs)
        if set(check_inputs) != set(unspent_outputs):
            print(f"Block {block_no} attempts to spend non-existent or already spent output")
            return False

    for transaction in regular_transactions:
        if not await transaction.verify(check_double_spend=False):
            print(f"Block {block_no} contains invalid transaction: {transaction.hash()}")
            return False

    # --- CONSENSUS CHECK: MERKLE ROOT ---
    expected_merkle_root = rules.calculate_merkle_tree(regular_transactions)
    if merkle_tree != expected_merkle_root:
        print(f"Block {block_no} Merkle root mismatch. "
              f"Expected: {expected_merkle_root}, Got: {merkle_tree}")
        return False

    print(f"Block {block_no} passed all checks (Consensus: {rules.version.name})")
    return True

# ============================================================================
# BLOCK CREATION
# ============================================================================

async def create_block(block_content: str, transactions: List[Transaction], last_block: dict = None) -> bool:
    """
    Create and commit a new block with version-appropriate validation.
    """
    await Manager.invalidate_difficulty()
    difficulty, last_block_from_db = await calculate_difficulty()
    mining_info = (difficulty, last_block_from_db)

    if not await check_block(block_content, transactions, mining_info=mining_info):
        return False

    regular_transactions = [
        tx for tx in transactions 
        if isinstance(tx, Transaction) and not isinstance(tx, CoinbaseTransaction)
    ]

    database = Database.instance
    block_no = last_block_from_db.get('id', 0) + 1
    block_hash = sha256(block_content)
    
    previous_hash, address, merkle_tree, content_time, content_difficulty, random = \
        split_block_content(block_content)
    
    fees = sum(tx.fees for tx in regular_transactions)
    block_reward = get_block_reward(block_no)
    
    coinbase_transaction = CoinbaseTransaction(
        block_hash, 
        address, 
        block_reward + fees
    )
    
    if not coinbase_transaction.outputs[0].verify():
        return False

    try:
        await database.add_block(
            block_no, block_hash, block_content, address, random, 
            content_difficulty, block_reward + fees, content_time
        )
        await database.add_transaction(coinbase_transaction, block_hash)
        
        if regular_transactions:
            await database.add_transactions(regular_transactions, block_hash)

        await database.add_unspent_transactions_outputs(
            regular_transactions + [coinbase_transaction]
        )
        
        if regular_transactions:
            await database.remove_pending_transactions_by_hash([
                tx.hash() for tx in regular_transactions
            ])
            await database.remove_unspent_outputs(regular_transactions)
            await database.remove_pending_spent_outputs(regular_transactions)

    except Exception as e:
        print(f'FATAL: Could not commit block {block_no} to database. '
              f'Rolling back. Error: {e}')
        await database.delete_block(block_no)
        return False

    print(f'Added block {block_no} with {len(regular_transactions)} transactions. '
          f'Reward: {block_reward}, Fees: {fees}')
    await Manager.invalidate_difficulty()
    return True


# ============================================================================
# MERKLE TREE CALCULATION (NEW FUNCTION)
# ============================================================================

def get_transactions_merkle_tree(transactions: List[Union[Transaction, str]], block_height: int) -> str:
    """
    Calculates the Merkle root for a set of transactions using the
    version-appropriate consensus rules for the given block height.
    """
    # Get the correct set of consensus rules from the engine
    rules = CONSENSUS_ENGINE.get_rules(block_height)
    
    # Delegate the calculation to the rules object
    return rules.calculate_merkle_tree(transactions)

# ============================================================================
# PUBLIC API FUNCTIONS
# ============================================================================

def get_consensus_version_info() -> dict:
    """
    Get information about consensus versions for network coordination.
    Useful for debugging and ensuring network-wide fork coordination.
    """
    return get_consensus_info()


async def validate_consensus_compatibility(peer_info: dict) -> bool:
    """
    Validate that a peer is compatible with our consensus rules.
    
    Args:
        peer_info: Dictionary containing peer's consensus information
        
    Returns:
        True if peer is compatible, False otherwise
    """
    our_info = get_consensus_info()
    
    # Check that peer has same activation heights for all versions
    if len(peer_info.get('activations', [])) != len(our_info['activations']):
        return False
    
    for our_activation, peer_activation in zip(
        our_info['activations'], 
        peer_info.get('activations', [])
    ):
        if our_activation['height'] != peer_activation['height']:
            return False
        if our_activation['version'] != peer_activation['version']:
            return False
    
    return True