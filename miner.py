#!/usr/bin/env python3
"""
A multi-process miner for the Denaro cryptocurrency.
"""

import hashlib
import sys
import time
import argparse
from math import ceil
from multiprocessing import Process

import requests

from denaro.constants import ENDIAN
from denaro.helpers import string_to_bytes, timestamp


# --- Constants ---
# Interval for reporting hashing speed (in hashes)
HASH_REPORT_INTERVAL = 5_000_000
# Time in seconds before workers are restarted with new block data
WORKER_REFRESH_SECONDS = 90
# Default node URL
DEFAULT_NODE_URL = 'http://127.0.0.1:3006/'


def get_transactions_merkle_tree(transactions: list[str]) -> str:
    """Calculates the Merkle root for a list of transaction hashes."""
    if not transactions:
        return hashlib.sha256(b'').hexdigest()
    
    transaction_bytes = [bytes.fromhex(tx) for tx in transactions]
    return hashlib.sha256(b''.join(transaction_bytes)).hexdigest()


def run_miner(
    worker_id: int,
    step: int,
    address: str,
    node_url: str,
    mining_info: dict
):
    """
    The core mining loop executed by each worker process.
    
    Args:
        worker_id: The starting nonce for this worker (e.g., 0 for worker 1).
        step: The step size for the nonce to avoid collisions between workers.
        address: The miner's wallet address for the reward.
        node_url: The URL of the Denaro node.
        mining_info: The dictionary containing data from the node's /get_mining_info.
    """
    difficulty = mining_info['difficulty']
    decimal = difficulty % 1
    last_block = mining_info['last_block']
    
    # Ensure last_block has default values if it's the genesis block
    last_block_hash = last_block.get('hash', (30_06_2005).to_bytes(32, ENDIAN).hex())
    last_block_id = last_block.get('id', 0)
    
    chunk = last_block_hash[-int(difficulty):]
    
    # Define the block validity check function based on difficulty
    charset = '0123456789abcdef'
    idifficulty = int(difficulty)
    
    if decimal > 0:
        count = ceil(16 * (1 - decimal))
        valid_chars = set(charset[:count])
        def check_block_is_valid(block_hash: str) -> bool:
            return block_hash.startswith(chunk) and block_hash[idifficulty] in valid_chars
    else:
        def check_block_is_valid(block_hash: str) -> bool:
            return block_hash.startswith(chunk)

    # Prepare the constant part of the block content
    address_bytes = string_to_bytes(address)
    txs = mining_info['pending_transactions_hashes']
    merkle_tree = get_transactions_merkle_tree(txs)
    
    # Validate transaction hashes format (basic check)
    assert all(len(tx) == 64 for tx in txs), "Invalid transaction hash format found."

    if worker_id == 0:
        print(f"Difficulty: {difficulty}")
        print(f"New Block Number: {last_block_id + 1}")
        print(f"Confirming {len(txs)} transactions")

    # Construct the block prefix
    prefix = (
        bytes.fromhex(last_block_hash) +
        address_bytes +
        bytes.fromhex(merkle_tree) +
        timestamp().to_bytes(4, byteorder=ENDIAN) +
        int(difficulty * 10).to_bytes(2, ENDIAN)
    )
    if len(address_bytes) == 33:
        prefix = (2).to_bytes(1, ENDIAN) + prefix

    start_time = time.time()
    nonce = worker_id
    
    while True:
        # Check a large number of nonces before re-checking time
        for _ in range(HASH_REPORT_INTERVAL):
            block_content = prefix + nonce.to_bytes(4, ENDIAN)
            block_hash = hashlib.sha256(block_content).hexdigest()
            if check_block_is_valid(block_hash):
                print(f"\nWorker {worker_id + 1}: Block found!")
                print(f"Block Content: {block_content.hex()}")
                print(f"Transactions: {','.join(txs)}")
                
                try:
                    payload = {
                        'block_content': block_content.hex(),
                        'txs': txs,
                        'id': last_block_id + 1
                    }
                    # Dynamic timeout based on number of transactions
                    timeout = 20 + int((len(txs) or 1) / 3)
                    r = requests.post(f"{node_url}push_block", json=payload, timeout=timeout)
                    r.raise_for_status()
                    response = r.json()
                    print(f"Node response: {response}")
                    if response.get('ok'):
                        print("BLOCK MINED SUCCESSFULLY!\n")
                    # Exit the entire program on success
                    sys.exit(0)
                except requests.exceptions.RequestException as e:
                    print(f"Error submitting block: {e}")
                # After finding a block, this worker's job is done for this round.
                return

            nonce += step

        # After a batch, check elapsed time and report hashrate
        elapsed_time = time.time() - start_time
        if elapsed_time > 0:
            hashrate = HASH_REPORT_INTERVAL / elapsed_time
            print(f"Worker {worker_id + 1}: {hashrate / 1000:.2f} kH/s")
        
        # If too much time has passed, the block is likely stale. Exit to get new data.
        if elapsed_time > WORKER_REFRESH_SECONDS:
            print(f"Worker {worker_id + 1}: Timed out, restarting to get new block data.")
            return

        start_time = time.time() # Reset timer for next hashrate calculation


def worker_process(
    start_nonce: int,
    step: int,
    address: str,
    node_url: str,
    mining_info: dict
):
    """
    A wrapper for the miner function to handle exceptions and retries within a process.
    """
    while True:
        try:
            run_miner(start_nonce, step, address, node_url, mining_info)
            # If run_miner returns, it means it timed out, so we break to be restarted by the main process
            break
        except Exception as e:
            print(f"Critical error in worker {start_nonce + 1}: {e}")
            # In case of unexpected errors, wait before retrying to avoid spamming
            time.sleep(5)
            # For this simple miner, we just exit the process. Main will restart it.
            break


def main():
    """
    Main function to parse arguments, fetch mining data, and manage worker processes.
    """
    parser = argparse.ArgumentParser(
        description="A multi-process CPU miner for the Denaro network.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('--address', '-a', help="The mining address to receive rewards.", required=True, type=str)
    parser.add_argument('--workers', '-w', help="The number of worker processes (CPU cores) to use.", type=int, default=1)
    parser.add_argument('--node', '-n', help="The URL of the Denaro node API.", default=DEFAULT_NODE_URL)
    args = parser.parse_args()

    # Ensure node URL ends with a slash
    node_url = args.node
    if not node_url.endswith('/'):
        node_url += '/'

    print(f"Starting miner for address: {args.address}")
    print(f"Using {args.workers} worker(s).")
    print(f"Connecting to node: {node_url}")

    while True:
        mining_info = None
        while mining_info is None:
            try:
                print("Fetching mining information from node...")
                r = requests.get(f"{node_url}get_mining_info", timeout=10)
                r.raise_for_status()
                mining_info = r.json().get('result')
                if not mining_info:
                    raise ValueError("Node response did not contain 'result' data.")
            except (requests.exceptions.RequestException, ValueError) as e:
                print(f"Error fetching data: {e}. Retrying in 5 seconds...")
                time.sleep(5)

        processes = []
        for i in range(args.workers):
            print(f"Starting worker n.{i+1}...")
            p = Process(
                target=worker_process,
                daemon=True,
                args=(i, args.workers, args.address, node_url, mining_info)
            )
            p.start()
            processes.append(p)
        
        # Wait for processes to finish or for a refresh cycle to complete
        while any(p.is_alive() for p in processes):
            time.sleep(1)

        print("All workers have stopped. Restarting with fresh block data...\n")
        # All processes have either found a block and exited, or timed out.
        # The loop will now fetch new mining_info and restart them.


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting miner.")
        sys.exit(0)