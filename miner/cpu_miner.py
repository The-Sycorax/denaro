#!/usr/bin/env python3
"""
A multi-process miner for the Denaro cryptocurrency.
This version includes inter-process communication to prevent stale block submissions
and correctly uses the Merkle root provided by the node.
It also supports mining a maximum number of blocks before exiting.

**Updated to better differentiate between a failed submission and a stale block.**
"""

import argparse
import hashlib
import sys
import time
from math import ceil
from multiprocessing import Event, Process, Value

import requests
import os

# Get the absolute path of the directory containing the current script.
dir_path = os.path.dirname(os.path.realpath(__file__))

# Add the project root to sys.path so `denaro` can be imported
project_root = os.path.abspath(os.path.join(dir_path, '..'))
sys.path.insert(0, project_root)

# Absolute import
from denaro.helpers import string_to_bytes, timestamp


# --- Constants ---
HASH_REPORT_INTERVAL = 5_000_000
WORKER_REFRESH_SECONDS = 90
DEFAULT_NODE_URL = 'http://127.0.0.1:3006/'

# --- NEW: Submission Status Codes ---
# We use these integer codes to communicate the result from a worker to the main process.
STATUS_PENDING = 0
STATUS_SUCCESS = 1
STATUS_STALE = 2
STATUS_FAILED = 3


def run_miner(
    worker_id: int,
    step: int,
    address: str,
    node_url: str,
    mining_info: dict,
    stop_event: Event,
    submission_status: Value  # UPDATED: Replaced success_flag with submission_status
):
    """
    The core mining loop executed by each worker process.
    """
    difficulty = mining_info['difficulty']
    decimal = difficulty % 1
    last_block = mining_info['last_block']
    
    last_block_hash = last_block.get('hash', (33_554_432).to_bytes(32, 'little').hex())
    last_block_id = last_block.get('id', 0)
    
    chunk = last_block_hash[-int(difficulty):]
    
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
    
    merkle_tree = mining_info['merkle_root']
    
    if worker_id == 0:
        print(f"Difficulty: {difficulty}")
        print(f"New Block Number: {last_block_id + 1}")
        print(f"Confirming {len(txs)} transactions")
        print(f"Using Merkle Root provided by node: {merkle_tree}")

    # Construct the block prefix using the node-provided Merkle root
    prefix = (
        bytes.fromhex(last_block_hash) +
        address_bytes +
        bytes.fromhex(merkle_tree) +
        timestamp().to_bytes(4, byteorder='little') +
        int(difficulty * 10).to_bytes(2, 'little')
    )
    if len(address_bytes) == 33:
        prefix = (2).to_bytes(1, 'little') + prefix

    start_time = time.time()
    nonce = worker_id
    
    while not stop_event.is_set():
        for _ in range(HASH_REPORT_INTERVAL):
            if stop_event.is_set(): 
                return

            block_content = prefix + nonce.to_bytes(4, 'little')
            block_hash = hashlib.sha256(block_content).hexdigest()
            if check_block_is_valid(block_hash):
                if stop_event.is_set():
                    return
                
                stop_event.set()

                print(f"\nWorker {worker_id + 1}: Potential block found! Submitting to node...")
                print(f"Block Content: {block_content.hex()}")
                print(f"Transactions: {','.join(txs)}")
                
                try:
                    payload = {
                        'block_content': block_content.hex(),
                        'txs': txs,
                        'id': last_block_id + 1
                    }
                    timeout = 20 + int((len(txs) or 1) / 3)
                    r = requests.post(f"{node_url}submit_block", json=payload, timeout=timeout)
                    r.raise_for_status()
                    response = r.json()

                    # --- UPDATED: Detailed response handling ---
                    if response.get('ok'):
                        print("Node Response: BLOCK MINED SUCCESSFULLY!\n")
                        submission_status.value = STATUS_SUCCESS
                    else:
                        # The node received our request but rejected the block. Let's find out why.
                        error_message = response.get('message', str(response)).lower()
                        if 'stale' in error_message or 'already in chain' in error_message:
                            print(f"Node Response: Block was stale. Another miner was faster.")
                            submission_status.value = STATUS_STALE
                        else:
                            print(f"Node Response: Block rejected for another reason: {response}")
                            submission_status.value = STATUS_FAILED

                except requests.exceptions.RequestException as e:
                    print(f"Error submitting block (network issue or node down): {e}")
                    submission_status.value = STATUS_FAILED
                
                return

            nonce += step

        elapsed_time = time.time() - start_time
        if elapsed_time > 0:
            hashrate = HASH_REPORT_INTERVAL / elapsed_time
            print(f"Worker {worker_id + 1}: {hashrate / 1000:.2f} kH/s")
        
        if elapsed_time > WORKER_REFRESH_SECONDS:
            print(f"Worker {worker_id + 1}: Refreshed work timer, restarting.")
            return

        start_time = time.time()

def worker_process(
    start_nonce: int,
    step: int,
    address: str,
    node_url: str,
    mining_info: dict,
    stop_event: Event,
    submission_status: Value # UPDATED
):
    """A wrapper for the miner function to handle exceptions within a process."""
    try:
        run_miner(start_nonce, step, address, node_url, mining_info, stop_event, submission_status)
    except Exception as e:
        print(f"Critical error in worker {start_nonce + 1}: {e}")
        submission_status.value = STATUS_FAILED # UPDATED
        stop_event.set()

def main():
    """
    Main function to parse arguments, fetch mining data, and manage worker processes.
    """
    parser = argparse.ArgumentParser(description="A multi-process CPU miner for the Denaro network.", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--address', '-a', help="The Denaro address to receive block rewards.", required=True, type=str)
    parser.add_argument('--workers', '-w', help="The number of worker processes to use.", type=int, default=1)
    parser.add_argument('--node', '-n', help="The URL or IP Address of the Denaro node.", default=DEFAULT_NODE_URL)
    parser.add_argument('--max-blocks', '-m', help="Max number of blocks to mine before exit.", type=int, default=None)
    args = parser.parse_args()

    node_url = args.node
    if not node_url.endswith('/'):
        node_url += '/'

    print(f"Starting miner for address: {args.address}")
    print(f"Using {args.workers} worker(s).")
    print(f"Connecting to node: {node_url}")
    if args.max_blocks:
        print(f"Will stop after mining {args.max_blocks} block(s).")

    mined_blocks_count = 0
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
        stop_event = Event()
        # UPDATED: Use a shared integer for status instead of a boolean
        submission_status = Value('i', STATUS_PENDING) 

        for i in range(args.workers):
            print(f"Starting worker n.{i+1}...")
            p = Process(
                target=worker_process,
                daemon=True,
                args=(i, args.workers, args.address, node_url, mining_info, stop_event, submission_status)
            )
            p.start()
            processes.append(p)
        
        while all(p.is_alive() for p in processes):
            if stop_event.is_set():
                time.sleep(0.1) # Give workers a moment to see the event and stop
                break
            time.sleep(0.5)

        for p in processes:
            if p.is_alive():
                p.terminate()
                p.join()

        # --- UPDATED: Main loop logic to handle different statuses ---
        status = submission_status.value
        
        if status == STATUS_SUCCESS:
            mined_blocks_count += 1
            max_blocks_str = args.max_blocks if args.max_blocks is not None else '∞'
            print(f"Total blocks mined: {mined_blocks_count} / {max_blocks_str}")

            if args.max_blocks is not None and mined_blocks_count >= args.max_blocks:
                print(f"Reached max number of blocks to mine ({args.max_blocks}). Exiting.")
                break
            
            print("Preparing for next block...\n")
            time.sleep(2)  # Wait for node to update before fetching new info

        elif status == STATUS_STALE:
            print("Block was stale (another miner was faster). This is normal. Restarting with fresh data...\n")
            time.sleep(2)

        elif status == STATUS_FAILED:
            print("Block submission failed due to an error. Restarting with fresh data...\n")
            time.sleep(2)
            
        else: # status == STATUS_PENDING
            # This case means all workers finished (likely timed out) without finding a block.
            print("All workers timed out. Restarting with fresh block data...\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting miner.")
        sys.exit(0)