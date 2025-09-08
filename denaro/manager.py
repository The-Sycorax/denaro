# denaro/manager.py
import hashlib
from decimal import Decimal
from io import BytesIO
from math import ceil, floor, log
from typing import Tuple, List, Union

from . import Database
from .constants import ENDIAN, MAX_BLOCK_SIZE_HEX
from .helpers import sha256, timestamp, bytes_to_string, string_to_bytes
from .transactions import CoinbaseTransaction, Transaction

# --- Canonical Chain Parameters ---
BLOCK_TIME = 180
BLOCKS_PER_ADJUSTMENT = 512
START_DIFFICULTY = Decimal('6.0')

def difficulty_to_hashrate(difficulty: Decimal) -> int:
    """Converts a difficulty score to the approximate hashrate required to solve a block."""
    decimal_part = difficulty % 1
    integer_part = floor(difficulty)
    return Decimal(16 ** integer_part) * (Decimal(16) / ceil(Decimal(16) * (Decimal(1) - decimal_part)))

def hashrate_to_difficulty(hashrate: int) -> Decimal:
    """Converts an approximate hashrate into a difficulty score."""
    if hashrate <= 0:
        return START_DIFFICULTY # Avoid log(0) errors

    integer_part = floor(log(hashrate, 16))
    ratio = hashrate / (16 ** integer_part)

    for i in range(10):
        decimal_step = Decimal(i) / 10
        coeff = Decimal(16) / ceil(Decimal(16) * (Decimal(1) - decimal_step))
        if coeff >= ratio:
            return Decimal(integer_part) + decimal_step

    return Decimal(integer_part) + Decimal('0.9')

async def calculate_difficulty() -> Tuple[Decimal, dict]:
    database = Database.instance
    last_block = await database.get_last_block()

    if last_block is None:
        return START_DIFFICULTY, {}

    last_block = dict(last_block)
    
    if last_block['id'] < BLOCKS_PER_ADJUSTMENT:
        return START_DIFFICULTY, last_block

    if last_block['id'] % BLOCKS_PER_ADJUSTMENT != 0:
        return last_block['difficulty'], last_block

    first_block_of_period = await database.get_block_by_id(last_block['id'] - BLOCKS_PER_ADJUSTMENT + 1)
    time_elapsed = last_block['timestamp'] - first_block_of_period['timestamp']
    if time_elapsed == 0:
        time_elapsed = 1
        
    avg_block_time = time_elapsed / BLOCKS_PER_ADJUSTMENT
    ratio = Decimal(BLOCK_TIME) / Decimal(avg_block_time)
    ratio = max(Decimal('0.25'), min(ratio, Decimal('4.0')))

    last_difficulty = last_block['difficulty']
    current_hashrate = difficulty_to_hashrate(last_difficulty)
    new_estimated_hashrate = current_hashrate * ratio
    new_difficulty = hashrate_to_difficulty(new_estimated_hashrate)

    print(f"Difficulty Adjustment at block {last_block['id']}:")
    print(f"  Time Elapsed: {time_elapsed}s for {BLOCKS_PER_ADJUSTMENT} blocks.")
    print(f"  Average Block Time: {avg_block_time:.2f}s (Target: {BLOCK_TIME}s)")
    print(f"  Adjustment Ratio: {ratio:.4f}")
    print(f"  Old Difficulty: {last_difficulty} -> New Difficulty: {new_difficulty}")

    return new_difficulty, last_block

async def get_difficulty() -> Tuple[Decimal, dict]:
    if Manager.difficulty is None:
        Manager.difficulty = await calculate_difficulty()
    return Manager.difficulty

async def check_block_is_valid(block_content: str, mining_info: tuple = None) -> bool:
    if mining_info is None:
        mining_info = await get_difficulty()
    difficulty, last_block = mining_info
    block_hash = sha256(block_content)

    if not last_block: # Genesis block case
        return True

    last_block_hash = last_block['hash']
    decimal = difficulty % 1
    difficulty = floor(difficulty)
    if decimal > 0:
        charset = '0123456789abcdef'
        count = ceil(16 * (1 - decimal))
        return block_hash.startswith(last_block_hash[-difficulty:]) and block_hash[difficulty] in charset[:count]
    return block_hash.startswith(last_block_hash[-difficulty:])

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
    MAX_HALVINGS = 64 # A long lifespan ensures a smooth transition to a fee-based security model.

    # --- Reward Calculation Logic ---

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

async def clear_pending_transactions(transactions=None):
    database: Database = Database.instance
    await database.clear_duplicate_pending_transactions()
    transactions = transactions or await database.get_pending_transactions_limit(hex_only=True)
    used_inputs = []
    for transaction in transactions:
        if isinstance(transaction, str):
            transaction = await Transaction.from_hex(transaction, check_signatures=False)
        tx_hash = transaction.hash()
        tx_inputs = [(tx_input.tx_hash, tx_input.index) for tx_input in transaction.inputs]
        if any(used_input in tx_inputs for used_input in used_inputs):
            await database.remove_pending_transaction(tx_hash)
            print(f'removed {tx_hash}')
            return await clear_pending_transactions()
        used_inputs += tx_inputs
    unspent_outputs = await database.get_unspent_outputs(used_inputs)
    double_spend_inputs = set(used_inputs) - set(unspent_outputs)
    if double_spend_inputs == set(used_inputs):
        await database.remove_pending_transactions()
    elif double_spend_inputs:
        await database.remove_pending_transactions_by_contains([tx_input[0] + bytes([tx_input[1]]).hex() for tx_input in double_spend_inputs])

def get_transactions_merkle_tree(transactions: List[Union[Transaction, str]]):
    """
    Calculates the Merkle root for a list of transactions.
    Hashes are sorted before concatenation to ensure a deterministic root.
    """
    tx_hashes = []
    for tx in transactions:
        if isinstance(tx, str):
            tx_hashes.append(tx)
        else:
            tx_hashes.append(tx.hash())

    sorted_hashes = sorted(tx_hashes)
    concatenated_hashes = "".join(sorted_hashes)
    return hashlib.sha256(concatenated_hashes.encode('utf-8')).hexdigest()

def get_transactions_size(transactions: List[Transaction]):
    return sum(len(transaction.hex()) for transaction in transactions)

def block_to_bytes(last_block_hash: str, block: dict) -> bytes:
    address_bytes = string_to_bytes(block['address'])
    version = bytes([])
    if len(address_bytes) != 64:
        version = bytes([2])
    return version + \
           bytes.fromhex(last_block_hash) + \
           address_bytes + \
           bytes.fromhex(block['merkle_tree']) + \
           block['timestamp'].to_bytes(4, byteorder=ENDIAN) + \
           int(float(block['difficulty']) * 10).to_bytes(2, ENDIAN) \
           + block['random'].to_bytes(4, ENDIAN)

def split_block_content(block_content: str):
    _bytes = bytes.fromhex(block_content)
    stream = BytesIO(_bytes)
    version = 1 if len(_bytes) == 138 else int.from_bytes(stream.read(1), ENDIAN)

    previous_hash = stream.read(32).hex()
    address = bytes_to_string(stream.read(64 if version == 1 else 33))
    merkle_tree = stream.read(32).hex()
    timestamp = int.from_bytes(stream.read(4), ENDIAN)
    difficulty = int.from_bytes(stream.read(2), ENDIAN) / Decimal(10)
    random = int.from_bytes(stream.read(4), ENDIAN)
    return previous_hash, address, merkle_tree, timestamp, difficulty, random

async def check_block(block_content: str, transactions: List[Transaction], mining_info: tuple = None):
    if mining_info is None:
        mining_info = await calculate_difficulty()
    
    difficulty, last_block = mining_info
    block_no = last_block.get('id', 0) + 1

    if not await check_block_is_valid(block_content, mining_info):
        print(f"Block {block_no} failed PoW validation.")
        return False

    previous_hash, address, merkle_tree, content_time, content_difficulty, random = split_block_content(block_content)

    last_block_hash = last_block.get('hash')
    if last_block_hash and previous_hash != last_block_hash:
        print(f"Block {block_no} has incorrect previous hash.")
        return False

    last_timestamp = last_block.get('timestamp', 0)
    if content_time <= last_timestamp:
        print(f"Block {block_no} timestamp is not greater than previous block.")
        return False
    if content_time > timestamp() + 120:
        print(f"Block {block_no} timestamp is too far in the future.")
        return False

    regular_transactions = [tx for tx in transactions if isinstance(tx, Transaction) and not isinstance(tx, CoinbaseTransaction)]
    
    if len(block_content) > MAX_BLOCK_SIZE_HEX:
        print(f"Block {block_no} content is too large.")
        return False
    if get_transactions_size(regular_transactions) > MAX_BLOCK_SIZE_HEX:
        print(f"Block {block_no} total transaction size is too large.")
        return False

    if regular_transactions:
        check_inputs = sum([[(tx_input.tx_hash, tx_input.index) for tx_input in tx.inputs] for tx in regular_transactions], [])
        if len(set(check_inputs)) != len(check_inputs):
            print(f"Block {block_no} contains internal double-spend.")
            return False
        
        database = Database.instance
        unspent_outputs = await database.get_unspent_outputs(check_inputs)
        if set(check_inputs) != set(unspent_outputs):
            print(f"Block {block_no} attempts to spend an already-spent or non-existent output.")
            return False

    for transaction in regular_transactions:
        if not await transaction.verify(check_double_spend=False):
            print(f"Block {block_no} contains an invalid transaction: {transaction.hash()}")
            return False

    expected_merkle_root = get_transactions_merkle_tree(regular_transactions)
    if merkle_tree != expected_merkle_root:
        print(f"Block {block_no} Merkle root does not match. Expected: {expected_merkle_root}, Got: {merkle_tree}")
        return False

    print(f"Block {block_no} passed all checks.")
    return True

async def create_block(block_content: str, transactions: List[Transaction], last_block: dict = None):
    Manager.difficulty = None
    difficulty, last_block_from_db = await calculate_difficulty()
    mining_info = (difficulty, last_block_from_db)

    if not await check_block(block_content, transactions, mining_info=mining_info):
        return False

    regular_transactions = [tx for tx in transactions if isinstance(tx, Transaction) and not isinstance(tx, CoinbaseTransaction)]

    database = Database.instance
    block_no = last_block_from_db.get('id', 0) + 1
    block_hash = sha256(block_content)
    
    previous_hash, address, merkle_tree, content_time, content_difficulty, random = split_block_content(block_content)
    
    fees = sum(tx.fees for tx in regular_transactions)
    block_reward = get_block_reward(block_no)
    
    coinbase_transaction = CoinbaseTransaction(block_hash, address, block_reward + fees)
    
    if not coinbase_transaction.outputs[0].verify():
        return False

    try:
        await database.add_block(block_no, block_hash, block_content, address, random, content_difficulty, block_reward + fees, content_time)
        await database.add_transaction(coinbase_transaction, block_hash)
        
        if regular_transactions:
            await database.add_transactions(regular_transactions, block_hash)

        await database.add_unspent_transactions_outputs(regular_transactions + [coinbase_transaction])
        
        if regular_transactions:
            await database.remove_pending_transactions_by_hash([tx.hash() for tx in regular_transactions])
            await database.remove_unspent_outputs(regular_transactions)
            await database.remove_pending_spent_outputs(regular_transactions)

    except Exception as e:
        print(f'FATAL: Could not commit block {block_no} to database. Rolling back. Error: {e}')
        await database.delete_block(block_no)
        return False

    print(f'Added block {block_no} with {len(regular_transactions)} transactions. Reward: {block_reward}, Fees: {fees}')
    Manager.difficulty = None
    return True

class Manager:
    difficulty: Tuple[float, dict] = None