#!/usr/bin/env python3

"""
MIT License

Copyright (c) 2025 The-Sycorax (https://github.com/The-Sycorax)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE+= OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

"""
Single-worker CUDA-accelerated miner for Denaro (PyCUDA JIT, RTX 4090 defaults).

Overview:
--------
- Single process, single "worker" (no multiprocessing).
- Uses PyCUDA to JIT-compile a SHA-256 mining kernel at runtime.
- Offloads nonce search to the GPU with interleaved stepping and batched iterations.
- Preserves original semantics:
    * block_content = prefix || nonce_le
    * prefix = [optional 0x02 if len(address)==33] ||
               [last_block_hash (32B)] || [address_bytes] || [merkle_root (32B)] ||
               [timestamp (4 LE)] || [difficulty*10 (2 LE)]
    * If fractional difficulty: next hex nibble constrained to allowed charset.
    * If integer: all 16 hex chars allowed for the next nibble.
- Submits candidate to node and handles SUCCESS / STALE / FAILED like the CPU miner.

Defaults tuned for RTX 4090 (Ada)
---------------------------------
- blocks: 1024
- threads: 512
- iters_per_thread: 20000
- gpu-arch: sm_89

Environment
-----------
If the compiler or runtime cannot find CUDA, set:
    export PATH=/usr/local/cuda/bin:${PATH}
    export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}

Usage
-----
python3 miner_cuda_pycuda_single.py \
    --address <ADDR> \
    --node http://127.0.0.1:3006/ \
    --max-blocks 1 \
    [--blocks 1024 --threads 512 --iters-per-thread 20000 --gpu-arch sm_89]
"""

import argparse
import os
import sys
import time
from math import ceil
from typing import Optional, Tuple

import requests

import pycuda.autoinit
import pycuda.driver as cuda
from pycuda.compiler import SourceModule
import numpy as np


from datetime import datetime, timezone
import base58



# --- Constants / Status Codes ---
WORKER_REFRESH_SECONDS = 90
DEFAULT_NODE_URL = 'http://127.0.0.1:3006/'

STATUS_PENDING = 0
STATUS_SUCCESS = 1
STATUS_STALE = 2
STATUS_FAILED = 3

# --- CUDA kernel (PyCUDA SourceModule) ---
CUDA_SOURCE = r"""
extern "C" {

#include <stdint.h>
#include <string.h>

#define ROTRIGHT(a,b) (((a) >> (b)) | ((a) << (32-(b))))
#define CH(x,y,z) (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define EP0(x) (ROTRIGHT(x,2) ^ ROTRIGHT(x,13) ^ ROTRIGHT(x,22))
#define EP1(x) (ROTRIGHT(x,6) ^ ROTRIGHT(x,11) ^ ROTRIGHT(x,25))
#define SIG0(x) (ROTRIGHT(x,7) ^ ROTRIGHT(x,18) ^ ((x) >> 3))
#define SIG1(x) (ROTRIGHT(x,17) ^ ROTRIGHT(x,19) ^ ((x) >> 10))

typedef unsigned char BYTE;
typedef uint32_t  WORD;

typedef struct {
    BYTE data[64];
    WORD datalen;
    unsigned long long bitlen;
    WORD state[8];
} SHA256_CTX;

__constant__ WORD dev_k[64];

__device__ __forceinline__ void sha256_transform(SHA256_CTX* ctx, const BYTE data[])
{
    WORD a,b,c,d,e,f,g,h,i,j,t1,t2,m[64];

    #pragma unroll 16
    for (i=0,j=0; i<16; ++i, j+=4)
        m[i] = ( (WORD)data[j] << 24 ) | ( (WORD)data[j+1] << 16 ) | ( (WORD)data[j+2] << 8 ) | (WORD)data[j+3];

    #pragma unroll 64
    for (; i<64; ++i)
        m[i] = SIG1(m[i-2]) + m[i-7] + SIG0(m[i-15]) + m[i-16];

    a = ctx->state[0]; b = ctx->state[1]; c = ctx->state[2]; d = ctx->state[3];
    e = ctx->state[4]; f = ctx->state[5]; g = ctx->state[6]; h = ctx->state[7];

    #pragma unroll 64
    for (i=0; i<64; ++i) {
        t1 = h + EP1(e) + CH(e,f,g) + dev_k[i] + m[i];
        t2 = EP0(a) + MAJ(a,b,c);
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

__device__ __forceinline__ void sha256_init(SHA256_CTX* ctx)
{
    ctx->datalen = 0;
    ctx->bitlen = 0;
    ctx->state[0]=0x6a09e667; ctx->state[1]=0xbb67ae85; ctx->state[2]=0x3c6ef372; ctx->state[3]=0xa54ff53a;
    ctx->state[4]=0x510e527f; ctx->state[5]=0x9b05688c; ctx->state[6]=0x1f83d9ab; ctx->state[7]=0x5be0cd19;
}

__device__ __forceinline__ void sha256_update(SHA256_CTX* ctx, const BYTE data[], size_t len)
{
    for (size_t i=0; i<len; ++i) {
        ctx->data[ctx->datalen] = data[i];
        if (++ctx->datalen == 64) {
            sha256_transform(ctx, ctx->data);
            ctx->bitlen += 512;
            ctx->datalen = 0;
        }
    }
}

__device__ __forceinline__ void sha256_final(SHA256_CTX* ctx, BYTE hash[])
{
    WORD i = ctx->datalen;

    if (ctx->datalen < 56) {
        ctx->data[i++] = 0x80;
        while (i < 56) ctx->data[i++] = 0;
    } else {
        ctx->data[i++] = 0x80;
        while (i < 64) ctx->data[i++] = 0;
        sha256_transform(ctx, ctx->data);
        for (i=0; i<56; ++i) ctx->data[i] = 0;
    }

    ctx->bitlen += (unsigned long long)ctx->datalen * 8ull;
    ctx->data[63] = (BYTE)(ctx->bitlen      );
    ctx->data[62] = (BYTE)(ctx->bitlen >>  8);
    ctx->data[61] = (BYTE)(ctx->bitlen >> 16);
    ctx->data[60] = (BYTE)(ctx->bitlen >> 24);
    ctx->data[59] = (BYTE)(ctx->bitlen >> 32);
    ctx->data[58] = (BYTE)(ctx->bitlen >> 40);
    ctx->data[57] = (BYTE)(ctx->bitlen >> 48);
    ctx->data[56] = (BYTE)(ctx->bitlen >> 56);
    sha256_transform(ctx, ctx->data);

    #pragma unroll
    for (i=0; i<4; ++i) {
        hash[i     ] = (ctx->state[0] >> (24 - i*8)) & 0xff;
        hash[i +  4] = (ctx->state[1] >> (24 - i*8)) & 0xff;
        hash[i +  8] = (ctx->state[2] >> (24 - i*8)) & 0xff;
        hash[i + 12] = (ctx->state[3] >> (24 - i*8)) & 0xff;
        hash[i + 16] = (ctx->state[4] >> (24 - i*8)) & 0xff;
        hash[i + 20] = (ctx->state[5] >> (24 - i*8)) & 0xff;
        hash[i + 24] = (ctx->state[6] >> (24 - i*8)) & 0xff;
        hash[i + 28] = (ctx->state[7] >> (24 - i*8)) & 0xff;
    }
}

// Uppercase hex encoding for hash comparison
__device__ __forceinline__ void sha256_to_hex_uc(const unsigned char* data, char* out64)
{
    const char hex[16] = { '0','1','2','3','4','5','6','7','8','9','A','B','C','D','E','F' };
    #pragma unroll
    for (int i=0; i<32; ++i) {
        out64[i*2    ] = hex[(data[i] >> 4) & 0xF];
        out64[i*2 + 1] = hex[(data[i]     ) & 0xF];
    }
}

__device__ __forceinline__ bool nibble_prefix_match(const char* hh, const unsigned char* chunk, unsigned len)
{
    #pragma unroll
    for (unsigned i=0; i<len; ++i) { if (hh[i] != (char)chunk[i]) return false; }
    return true;
}

__device__ __forceinline__ bool bytes_contains_uc(const unsigned char* arr, size_t n, unsigned char v)
{
    #pragma unroll
    for (size_t i=0; i<n; ++i) { if (arr[i] == v) return true; }
    return false;
}

// Miner kernel for single-host-worker scenario.
// Nonce stepping:
//   start_offset = 0 for single worker
//   global_step  = blocks * threads
//   base_offset  = batch_idx * iters_per_thread * global_step
// Each thread starts at: i = start_offset + tid + base_offset
// and advances by global_step per iteration.
__global__ void miner_kernel(
    const unsigned char* __restrict__ hash_prefix,
    size_t prefix_len,
    const unsigned char* __restrict__ last_chunk,
    unsigned idiff,
    const unsigned char* __restrict__ charset,
    unsigned charset_len,
    unsigned int* __restrict__ result,     // 0xFFFFFFFF initially; set to nonce if found
    uint32_t start_offset,
    uint32_t global_step,
    uint32_t base_offset,
    uint32_t iters_per_thread
) {
    uint32_t tid = blockIdx.x * blockDim.x + threadIdx.x;
    uint32_t i = start_offset + tid + base_offset;

    const size_t temp_size = prefix_len + 4;
    unsigned char temp[320];
    unsigned char digest[32];
    char hexhash[64];

    for (size_t k=0; k<prefix_len; ++k) temp[k] = hash_prefix[k];
    unsigned char* nonce_ptr = temp + prefix_len;

    for (uint32_t it=0; it<iters_per_thread; ++it) {
        if (*result != 0xFFFFFFFFu) return; // another thread found a result

        memcpy(nonce_ptr, &i, 4);

        SHA256_CTX ctx;
        sha256_init(&ctx);
        sha256_update(&ctx, temp, temp_size);
        sha256_final(&ctx, digest);

        sha256_to_hex_uc(digest, hexhash);

        if ((idiff == 0 || nibble_prefix_match(hexhash, last_chunk, idiff)) &&
            (charset_len == 16 || bytes_contains_uc(charset, charset_len, (unsigned char)hexhash[idiff])))
        {
            atomicCAS(result, 0xFFFFFFFFu, i);
            return;
        }

        i += global_step;
    }
}

} // extern "C"
"""

# Host-side K constants for dev_k
K_CONST_HOST = np.array([
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
], dtype=np.uint32)

# ---------- Miner helpers ---------- #

def build_prefix(last_block_hash_hex: str, address_bytes: bytes, merkle_root_hex: str, difficulty: float) -> bytes:
    """
    Build constant block prefix (no nonce). Matches original CPU miner exactly.
    """
    last_block_hash = bytes.fromhex(last_block_hash_hex)
    merkle_root = bytes.fromhex(merkle_root_hex)
    difficulty_scaled = int(difficulty * 10).to_bytes(2, 'little')
    base = (
        last_block_hash +
        address_bytes +
        merkle_root +
        timestamp().to_bytes(4, 'little') +
        difficulty_scaled
    )
    if len(address_bytes) == 33:
        base = (2).to_bytes(1, 'little') + base
    return base


def compute_fractional_charset(difficulty: float) -> Tuple[int, str]:
    """
    Returns (idiff, allowed_charset_upper).
    """
    decimal = difficulty % 1
    idiff = int(difficulty)
    if decimal > 0:
        count = ceil(16 * (1 - decimal))
        allowed = '0123456789ABCDEF'[:count]
    else:
        allowed = '0123456789ABCDEF'
    return idiff, allowed


def make_last_block_chunk(last_block_hash_hex: str, idiff: int) -> str:
    """
    Returns the suffix of last_block_hash with length idiff (uppercase hex).
    """
    chunk = last_block_hash_hex[-idiff:] if idiff > 0 else ''
    return chunk.upper()

def string_to_bytes(string: str) -> bytes:
    try:
        point_bytes = bytes.fromhex(string)
    except ValueError:
        point_bytes = base58.b58decode(string)
    return point_bytes

def timestamp():
    return int(datetime.now(timezone.utc).replace(tzinfo=timezone.utc).timestamp())

def submit_block(node_url: str, last_block_id: int, txs, block_content: bytes) -> int:
    """
    Submit a candidate block to the node and return a STATUS_* code.
    """
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

        if response.get('ok'):
            print("Node Response: BLOCK MINED SUCCESSFULLY!\n")
            return STATUS_SUCCESS
        else:
            error_message = str(response.get('message', response)).lower()
            if 'stale' in error_message or 'already in chain' in error_message:
                print("Node Response: Block was stale. Another miner was faster.")
                return STATUS_STALE
            else:
                print(f"Node Response: Block rejected: {response}")
                return STATUS_FAILED

    except requests.exceptions.RequestException as e:
        print(f"Error submitting block (network issue or node down): {e}")
        return STATUS_FAILED

def prepend_env_path_if_not_set(env_variable_name, path_to_prepend):
    """
    Prepends a given path to an environment variable if it's not already present.

    Args:
        env_variable_name (str): The name of the environment variable (e.g., "PATH").
        path_to_prepend (str): The path string to prepend.
    """
    current_env_value = os.environ.get(env_variable_name, "")
    # Split the current environment variable value by the OS-specific path separator
    # Filter out any empty strings that might result from "::" or leading/trailing separators
    existing_paths = [p for p in current_env_value.split(os.pathsep) if p]

    if path_to_prepend not in existing_paths:
        if current_env_value: # If there was an existing value
            new_env_value = f"{path_to_prepend}{os.pathsep}{current_env_value}"
        else: # If the env var was empty or not set
            new_env_value = path_to_prepend
        os.environ[env_variable_name] = new_env_value
        print(f"Updated {env_variable_name}: Prepended '{path_to_prepend}'")
    else:
        print(f"{env_variable_name}: '{path_to_prepend}' is already present. No changes made.")

def main():
    """
    Parse args, compile CUDA kernel (once), and run single-worker mining loop with batched GPU searches.
    """
        # --- For PATH ---
    print(f"Original PATH: {os.environ.get('PATH', 'Not Set')}")
    prepend_env_path_if_not_set("PATH", "/usr/local/cuda/bin")
    print(f"Current PATH: {os.environ.get('PATH', 'Not Set')}\n")
    
    
    # --- For LD_LIBRARY_PATH ---
    print(f"Original LD_LIBRARY_PATH: {os.environ.get('LD_LIBRARY_PATH', 'Not Set')}")
    prepend_env_path_if_not_set("LD_LIBRARY_PATH", "/usr/local/cuda/lib64")
    print(f"Current LD_LIBRARY_PATH: {os.environ.get('LD_LIBRARY_PATH', 'Not Set')}")


    parser = argparse.ArgumentParser(
        description="Single-worker CUDA miner for the Denaro network (PyCUDA JIT; RTX 4090 defaults).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('--address', '-a', required=True, type=str, help="Mining address to receive rewards.")
    parser.add_argument('--node', '-n', default=DEFAULT_NODE_URL, help="URL of the Denaro node API.")
    parser.add_argument('--max-blocks', '-m', type=int, default=1, help="Max number of blocks to mine before exit.")
    parser.add_argument('--gpu-blocks', dest="gpu_blocks", type=int, default=256, help="CUDA grid blocks per launch.")
    parser.add_argument('--gpu-threads', dest="gpu_threads", type=int, default=256, help="CUDA threads per block.")
    parser.add_argument('--gpu-iterations', dest="gpu_iterations", type=int, default=10000, help="Iterations per thread per kernel batch.")
    parser.add_argument('--gpu-arch', dest="gpu_arch", required=True, help="nvcc --gpu-architecture value. Refer to: https://arnon.dk/matching-sm-architectures-arch-and-gencode-for-various-nvidia-cards/")

    args = parser.parse_args()

    node_url = args.node
    if not node_url.endswith('/'):
        node_url += '/'

    print(f"Starting CUDA miner (single worker) for address: {args.address}")
    print(f"Connecting to node: {node_url}")
    print(f"GPU launch dims: blocks={args.gpu_blocks}, threads={args.gpu_threads}, iters_per_thread={args.gpu_iterations}")
    print(f"GPU architecture flag: {args.gpu_arch}")  # display robustness
    if args.max_blocks:
        print(f"Will stop after mining {args.max_blocks} block(s).")

    # Compile CUDA module once
    options = ["-O3"]
    if args.gpu_arch:
        options.append(f"--gpu-architecture={args.gpu_arch}")
        options.append("--ptxas-options=-v")
    module = SourceModule(CUDA_SOURCE, options=options, no_extern_c=False)

    # Load constants/symbols/functions
    dev_k_sym, _ = module.get_global("dev_k")
    cuda.memcpy_htod(dev_k_sym, K_CONST_HOST)

    miner_kernel = module.get_function("miner_kernel")

    mined_blocks_count = 0

    while True:
        # Fetch mining info
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

        # Prepare mining inputs
        difficulty = mining_info['difficulty']
        last_block = mining_info['last_block']
        last_block_hash_hex = last_block.get('hash', (33_554_432).to_bytes(32, 'little').hex())
        last_block_id = last_block.get('id', 0)
        txs = mining_info['pending_transactions_hashes']
        merkle_root_hex = mining_info['merkle_root']
        address_bytes = string_to_bytes(args.address)

        print(f"Difficulty: {difficulty}")
        print(f"New Block Number: {last_block_id + 1}")
        print(f"Confirming {len(txs)} transactions")
        print(f"Using Merkle Root provided by node: {merkle_root_hex}")

        prefix_bytes = build_prefix(last_block_hash_hex, address_bytes, merkle_root_hex, difficulty)
        idiff, allowed_charset = compute_fractional_charset(difficulty)
        last_chunk_uc = make_last_block_chunk(last_block_hash_hex, idiff)

        # Device buffers
        d_prefix = cuda.mem_alloc(len(prefix_bytes))
        cuda.memcpy_htod(d_prefix, prefix_bytes)

        # idiff==0: allocate minimal 1-byte buffer; kernel won't read it
        d_last_chunk = cuda.mem_alloc(max(1, idiff))
        if idiff > 0:
            cuda.memcpy_htod(d_last_chunk, last_chunk_uc.encode('ascii'))

        charset_bytes = allowed_charset.encode('ascii')
        d_charset = cuda.mem_alloc(len(charset_bytes))
        cuda.memcpy_htod(d_charset, charset_bytes)

        d_result = cuda.mem_alloc(np.uint32().nbytes)

        # Search parameters for single worker
        start_offset = np.uint32(0)
        global_step = np.uint32(args.gpu_blocks * args.gpu_threads)

        # Batch loop within refresh window
        start_time = time.time()
        batch_idx = 0
        found_nonce: Optional[int] = None

        result_host = np.array([0xFFFFFFFF], dtype=np.uint32)

        while (time.time() - start_time) < WORKER_REFRESH_SECONDS:
            # Reset result
            result_host[0] = np.uint32(0xFFFFFFFF)
            cuda.memcpy_htod(d_result, result_host)

            base_offset = np.uint32((batch_idx * args.gpu_iterations) * int(global_step) & 0xFFFFFFFF)

            # Launch kernel
            miner_kernel(
                d_prefix,
                np.uint64(len(prefix_bytes)),
                d_last_chunk,
                np.uint32(idiff),
                d_charset,
                np.uint32(len(charset_bytes)),
                d_result,
                start_offset,
                global_step,
                base_offset,
                np.uint32(args.gpu_iterations),
                block=(args.gpu_threads, 1, 1),
                grid=(args.gpu_blocks, 1),
            )

            cuda.Context.synchronize()

            # Check result
            cuda.memcpy_dtoh(result_host, d_result)
            if result_host[0] != np.uint32(0xFFFFFFFF):
                found_nonce = int(result_host[0])
                break

            batch_idx += 1

        # Free per-round buffers (context persists, module persists)
        d_prefix.free()
        d_last_chunk.free()
        d_charset.free()
        d_result.free()

        if found_nonce is None:
            print("No solution in this window. Refreshing mining info...\n")
            continue

        block_content = prefix_bytes + int(found_nonce).to_bytes(4, 'little')
        print("\nPotential block found! Submitting to node...")
        print(f"Block Content: {block_content.hex()}")
        print(f"Transactions: {','.join(txs)}")

        status = submit_block(node_url=node_url, last_block_id=last_block_id, txs=txs, block_content=block_content)

        if status == STATUS_SUCCESS:
            mined_blocks_count += 1
            max_blocks_str = args.max_blocks if args.max_blocks is not None else '∞'
            print(f"Total blocks mined: {mined_blocks_count} / {max_blocks_str}")
            if args.max_blocks is not None and mined_blocks_count >= args.max_blocks:
                print(f"Reached max number of blocks to mine ({args.max_blocks}). Exiting.")
                break
            print("Preparing for next block...\n")
            time.sleep(2)

        elif status == STATUS_STALE:
            print("Block was stale (another miner was faster). Restarting with fresh data...\n")
            time.sleep(2)

        else:  # STATUS_FAILED
            print("Block submission failed due to an error. Restarting with fresh data...\n")
            time.sleep(2)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting miner.")
        sys.exit(0)