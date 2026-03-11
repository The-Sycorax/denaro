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

Usage Example
-----
python3 cuda_miner.py \
    --address <ADDR> \
    --node http://127.0.0.1:3006/ \
    --max-blocks 1 \
    --gpu-blocks 1024 \
    --gpu-threads 512 \
    --gpu-iterations 20000 \
    --gpu-arch sm_89 \
    --verbose \
    --no-tui
"""

import argparse
import logging
import os
import sys
import threading
import time
from collections import deque
from math import ceil
from pathlib import Path
from queue import Empty, Full, Queue
from shutil import get_terminal_size
from typing import List, Optional, Tuple

import requests

import pycuda.autoinit
import pycuda.driver as cuda
from pycuda.compiler import SourceModule
import numpy as np

import base58

from humanfriendly import format_timespan

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Log, Static


# --- Constants / Status Codes ---
WORKER_REFRESH_SECONDS = 5
POLLING_INTERVAL_SECONDS = 2.0      # Intentionally shorter than WORKER_REFRESH_SECONDS
                                    # so chain advances are detected promptly.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0
MAX_NONCE_SPACE = 1 << 32           # Full 32-bit nonce range: 4,294,967,296
DEFAULT_NODE_URL = 'http://127.0.0.1:3006/'

STATUS_PENDING = 0
STATUS_SUCCESS = 1
STATUS_STALE = 2
STATUS_FAILED = 3

# Configure logger
logger = logging.getLogger(__name__)


class MiningStats:
    """Container for mining runtime statistics.

    Hash rate is computed from a rolling time window so it reflects current
    GPU throughput rather than a stale session-lifetime average.  All mutation
    goes through the public API; callers never access internal fields directly.
    """

    # Width of the rolling hash-rate measurement window in seconds.
    _RATE_WINDOW_SECONDS = 5

    _HEADER_LINES = [
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓",
        "┃                       Denaro CUDA Miner                       ┃",
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
    ]

    _STATS_LINES = [
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓",
        "┃ - Difficulty: {}{}┃",
        "┃ - Current Block Height: {}{}┃",
        "┃ - Blocks Mined: {}{}┃",
        "┃                                                               ┃",
        "┃ - Hash Rate: {}{}┃",
        "┃ - Time Elapsed: {}{}┃",
        "┃                                                               ┃",
        "┃ - Status: {}{}┃",
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
    ]

    def __init__(self, max_blocks: Optional[int] = None) -> None:
        """Initialise mining statistics.

        Args:
            max_blocks: Maximum blocks to mine, or None for unlimited.
        """
        self._lock = threading.Lock()
        self.difficulty: float = 0.0
        self.block_number: int = 0
        self.blocks_mined: int = 0
        self.max_blocks: Optional[int] = max_blocks
        self.last_status: str = "Idle"

        self.start_time: float = 0.0
        self.total_hashes: int = 0
        # Rolling window: deque of (wall_time, cumulative_total_hashes) samples.
        self._rate_window: deque = deque()
        self._session_finished: bool = False
        self._session_end_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------

    def start_session(self) -> None:
        """Mark the start of a mining session."""
        with self._lock:
            self.start_time = time.time()
            self.total_hashes = 0
            self._rate_window.clear()
            self._session_finished = False
            self._session_end_time = None

    def start_block(self, block_number: int, difficulty: float) -> None:
        """Record the block being mined and its difficulty.

        Args:
            block_number: The block number being mined.
            difficulty: The mining difficulty.
        """
        with self._lock:
            self.block_number = block_number
            self.difficulty = difficulty

    def update_status(self, status: str) -> None:
        """Update the current mining status string.

        Args:
            status: Human-readable status message.
        """
        with self._lock:
            self.last_status = status

    def increment_blocks_mined(self) -> None:
        """Increment the count of successfully mined blocks."""
        with self._lock:
            self.blocks_mined += 1

    def add_hashes(self, n: int) -> None:
        """Record that n additional nonces have been evaluated.

        This is the only correct way to report GPU work; callers must not
        mutate total_hashes directly.  Internally this appends a timestamped
        sample to the rolling window used for hash-rate calculation, and prunes
        samples that have aged past _RATE_WINDOW_SECONDS.

        Args:
            n: Number of nonces evaluated in the most recently completed batch.
        """
        with self._lock:
            self.total_hashes += n
            now = time.time()
            self._rate_window.append((now, self.total_hashes))
            # Always keep at least two samples so the rate calculation has a
            # baseline even when the window has just started.
            cutoff = now - self._RATE_WINDOW_SECONDS
            while len(self._rate_window) > 2 and self._rate_window[0][0] < cutoff:
                self._rate_window.popleft()

    def finish_session(self) -> None:
        """Mark the mining session as finished and freeze elapsed-time display."""
        with self._lock:
            if not self._session_finished:
                self._session_end_time = time.time() if self.start_time > 0 else None
                self._session_finished = True

    # ------------------------------------------------------------------
    # Read-only helpers (called under lock inside format_panel)
    # ------------------------------------------------------------------

    def _live_hash_rate(self, now: float) -> float:
        if len(self._rate_window) < 2:
            return 0.0
        t0, c0 = self._rate_window[0]
        _, c_last = self._rate_window[-1]
        dt = now - t0
        if dt <= 0.0:
            return 0.0
        return (c_last - c0) / dt

    @staticmethod
    def _format_hash_rate(hash_rate: float) -> str:
        """Format a hash-rate value using conventional SI suffixes."""
        if hash_rate >= 1_000_000:
            return f"{hash_rate / 1_000_000:.2f} MH/s"
        if hash_rate >= 1_000:
            return f"{hash_rate / 1_000:.2f} kH/s"
        return f"{hash_rate:.2f} H/s"

    def format_panel(self, available_width: Optional[int] = None) -> str:
        """Render statistics for the TUI stats panel using framed ASCII art."""
        current_time = time.time()
        with self._lock:
            inner_width = 62

            max_blocks = self.max_blocks if self.max_blocks is not None else "∞"
            blocks_line = f"{self.blocks_mined:,} / {max_blocks}"
            difficulty_line = f"{self.difficulty:.2f}"

            if self.start_time > 0:
                if self._session_finished and self._session_end_time is not None:
                    time_elapsed = self._session_end_time - self.start_time
                else:
                    time_elapsed = current_time - self.start_time
            else:
                time_elapsed = 0.0
            elapsed_line = format_timespan(int(time_elapsed))

            # Use live rate: denominator extends to current wall time so the
            # value updates every render frame, not just on batch completion.
            hash_rate = 0.0 if self._session_finished else self._live_hash_rate(current_time)
            hash_rate_line = self._format_hash_rate(hash_rate)

            padding_difficulty = " " * (inner_width - len("- Difficulty: ") - len(difficulty_line))
            padding_block = " " * (inner_width - len("- Current Block Height: ") - len(str(self.block_number)))
            padding_blocks = " " * (inner_width - len("- Blocks Mined: ") - len(blocks_line))
            padding_hash = " " * (inner_width - len("- Hash Rate: ") - len(hash_rate_line))
            padding_elapsed = " " * (inner_width - len("- Time Elapsed: ") - len(elapsed_line))
            padding_status = " " * (inner_width - len("- Status: ") - len(self.last_status))

            header_message = "\n".join(self._HEADER_LINES)
            stats_message = "\n".join(self._STATS_LINES).format(
                difficulty_line, padding_difficulty,
                self.block_number-1, padding_block,
                blocks_line, padding_blocks,
                hash_rate_line, padding_hash,
                elapsed_line, padding_elapsed,
                self.last_status, padding_status
            )

            terminal_width = available_width if available_width else get_terminal_size().columns
            adjusted_header = "\n".join(line[:terminal_width] for line in header_message.split("\n"))
            adjusted_stats = "\n".join(line[:terminal_width] for line in stats_message.split("\n"))
            return f"{adjusted_header}\n{adjusted_stats}"


class TUILogHandler(logging.Handler):
    """Logging handler that forwards log records to a queue for the TUI."""

    def __init__(self, log_queue: Queue, *, formatter: Optional[logging.Formatter] = None) -> None:
        super().__init__()
        self._log_queue = log_queue
        if formatter is not None:
            self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - UI glue
        try:
            message = self.format(record)
            self._log_queue.put_nowait((record.levelno, message))
        except Full:
            # Drop log line if queue is saturated to avoid blocking GPU work.
            pass


class MinerTUI(App):  # pragma: no cover - requires interactive terminal
    """Textual TUI that renders miner logs and live statistics side-by-side."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #stats-panel {
        height: auto;
        border: solid $primary;
        padding: 1;
    }

    #log-panel {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }

    Log {
        background: $surface;
    }

    #stats-view {
        height: auto;
        min-height: 9;
    }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, log_queue: Queue, stats: MiningStats, stop_event: threading.Event) -> None:
        super().__init__()
        self._log_queue = log_queue
        self._stats = stats
        self._stop_event = stop_event
        self._log_widget: Optional[Log] = None
        self._stats_widget: Optional[Static] = None
        self._drain_active: bool = False

    def compose(self) -> ComposeResult:
        yield Container(Static("Waiting for miner...", id="stats-view"), id="stats-panel")
        yield Container(Log(id="log-view"), id="log-panel")

    def on_mount(self) -> None:
        self._log_widget = self.query_one("#log-view", Log)
        self._stats_widget = self.query_one("#stats-view", Static)
        self._drain_active = True
        self._schedule_log_drain()
        self._schedule_stats_update()

    def action_quit(self) -> None:
        self._stop_event.set()
        self._drain_active = False
        self.exit()

    def _schedule_log_drain(self) -> None:
        if not self._drain_active:
            return
        processed = self._process_log_queue()
        if processed:
            self.call_next(self._schedule_log_drain)
        else:
            self.set_timer(0.05, self._schedule_log_drain)

    def _process_log_queue(self) -> bool:
        if self._stop_event.is_set() and self._log_queue.empty():
            self.exit()
            return False

        processed_any = False
        if self._log_widget is not None:
            while True:
                try:
                    level, message = self._log_queue.get_nowait()
                except Empty:
                    break
                else:
                    lines = message.split('\n')
                    trailing_blank = message.endswith('\n')
                    if trailing_blank and lines and lines[-1] == "":
                        lines = lines[:-1]
                    for line in lines:
                        self._log_widget.write_line(line)
                    if trailing_blank:
                        self._log_widget.write_line("\u200B")
                    processed_any = True
        return processed_any

    def _schedule_stats_update(self) -> None:
        if not self._drain_active:
            return
        self._update_stats_panel()
        self.set_timer(0.1, self._schedule_stats_update)

    def _update_stats_panel(self) -> None:
        if self._stats_widget is None:
            return
        width = self._stats_widget.size.width or self.size.width or get_terminal_size().columns
        self._stats_widget.update(self._stats.format_panel(width))


class Miner:
    """CUDA miner that performs GPU-accelerated block mining."""

    KERNEL_PATH = Path(__file__).resolve().parent / "kernel" / "cuda_miner_kernel.cu"

    _MAX_PREFIX_BYTES = 128
    _MAX_LAST_CHUNK_BYTES = 64
    _MAX_CHARSET_BYTES = 16

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

    def __init__(
        self,
        address: str,
        node_url: str,
        gpu_blocks: int,
        gpu_threads: int,
        gpu_iterations: int,
        gpu_arch: str,
        max_blocks: Optional[int] = None,
        stats: Optional[MiningStats] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self._address = address
        self._node_url = node_url
        self._gpu_blocks = gpu_blocks
        self._gpu_threads = gpu_threads
        self._gpu_iterations = gpu_iterations
        self._gpu_arch = gpu_arch
        self._max_blocks = max_blocks
        self._stop_event = stop_event
        self._stats = stats if stats is not None else MiningStats(max_blocks=max_blocks)
        self._miner_kernel = None
        self._context_pushed = False
        self._block_refresh_event = threading.Event()
        self._polling_stop_event = threading.Event()
        self._current_block_target_lock = threading.Lock()
        self._current_block_target: Optional[int] = None
        self._latest_network_block_id: Optional[int] = None
        self._polling_thread: Optional[threading.Thread] = None
        self._last_refresh_logged_height: Optional[int] = None

        # Predecode address once; reused across all blocks.
        self._address_bytes = self._string_to_bytes(self._address)

        # Nonces covered per kernel launch.
        self._nonces_per_batch = self._gpu_blocks * self._gpu_threads * self._gpu_iterations

        # Maximum batches before the full 32-bit nonce space is covered.
        # Minimum of 1 so at least one batch always runs even when a single
        # batch already exceeds the 4 GH nonce space (e.g. RTX 4090 defaults).
        self._max_batches = max(1, ceil(MAX_NONCE_SPACE / self._nonces_per_batch))

        # Device buffers (allocated lazily after the CUDA context is ready).
        self._d_prefix: Optional[cuda.DeviceAllocation] = None
        self._d_last_chunk: Optional[cuda.DeviceAllocation] = None
        self._d_charset: Optional[cuda.DeviceAllocation] = None

        # Double-buffered result pairs: two device allocations, two pinned host
        # arrays, and two CUDA streams so the CPU can evaluate one completed
        # result while the GPU runs the next batch concurrently.
        self._d_results: List[Optional[cuda.DeviceAllocation]] = [None, None]
        self._result_hosts: List[Optional[np.ndarray]] = [None, None]
        self._streams: List[Optional[cuda.Stream]] = [None, None]

    # ------------------------------------------------------------------
    # Block-height coordination
    # ------------------------------------------------------------------

    def _prepare_for_new_block(self, target_height: int) -> None:
        """Record the target height and clear any stale refresh signal."""
        with self._current_block_target_lock:
            self._current_block_target = target_height
            self._block_refresh_event.clear()

    def _clear_current_block_target(self) -> None:
        """Reset coordination state when the miner becomes idle."""
        with self._current_block_target_lock:
            self._current_block_target = None

    # ------------------------------------------------------------------
    # HTTP polling listener
    # ------------------------------------------------------------------

    def _start_polling_listener(self) -> None:
        """Start the background HTTP polling thread if not already running.

        Polls /get_status at POLLING_INTERVAL_SECONDS — deliberately shorter
        than WORKER_REFRESH_SECONDS — so chain advances are detected and acted
        on faster than the kernel refresh window.
        """
        if self._polling_thread is not None and self._polling_thread.is_alive():
            return

        self._polling_stop_event.clear()
        self._polling_thread = threading.Thread(
            target=self._run_polling_listener,
            name="denaro-polling-listener",
            daemon=True,
        )
        self._polling_thread.start()

    def _run_polling_listener(self) -> None:
        """Poll /get_status and fire the block-refresh event when the chain advances."""
        while not self._polling_stop_event.is_set():
            try:
                response = requests.get(f"{self._node_url}get_status", timeout=10)
                response.raise_for_status()
                status_data = response.json().get('result')

                if status_data:
                    block_id = status_data.get('height')
                    if isinstance(block_id, int) and block_id >= 0:
                        should_trigger = False
                        should_log = False

                        with self._current_block_target_lock:
                            self._latest_network_block_id = block_id
                            target = self._current_block_target
                            if target is not None and block_id >= target:
                                should_trigger = True
                                if self._last_refresh_logged_height != block_id:
                                    self._last_refresh_logged_height = block_id
                                    should_log = True

                        if should_trigger:
                            self._block_refresh_event.set()
                            if should_log:
                                block_hash = status_data.get('last_block_hash', 'unknown')
                                logger.info(f"Detected new block height from node: {block_id}")
                                logger.info("Refreshing work...\n")
            except Exception as exc:
                if not self._polling_stop_event.is_set():
                    logger.debug("HTTP polling error: %s", exc)

            if self._polling_stop_event.wait(POLLING_INTERVAL_SECONDS):
                break

    def _shutdown_polling_listener(self) -> None:
        """Signal the polling thread to stop and wait for it to exit."""
        self._polling_stop_event.set()
        if self._polling_thread is not None:
            self._polling_thread.join(timeout=5.0)
            self._polling_thread = None

    # ------------------------------------------------------------------
    # CUDA resource management
    # ------------------------------------------------------------------

    def _alloc_device_buffers(self) -> None:
        """Allocate per-block device buffers, pinned host buffers, and CUDA streams.

        Two result-buffer pairs are created to support double-buffering: while
        the GPU evaluates batch N on stream[cur], the CPU can read the completed
        result for batch N-1 from stream[prev] without stalling the pipeline.
        """
        if self._d_results[0] is not None:
            return

        self._d_prefix = cuda.mem_alloc(self._MAX_PREFIX_BYTES)
        self._d_last_chunk = cuda.mem_alloc(self._MAX_LAST_CHUNK_BYTES)
        self._d_charset = cuda.mem_alloc(self._MAX_CHARSET_BYTES)

        for i in range(2):
            self._d_results[i] = cuda.mem_alloc(np.uint32().nbytes)
            self._result_hosts[i] = cuda.pagelocked_empty(1, dtype=np.uint32)
            self._streams[i] = cuda.Stream()

    def _free_device_buffers(self) -> None:
        """Free all cached device allocations and release CUDA streams."""
        for attr in ("_d_prefix", "_d_last_chunk", "_d_charset"):
            buf = getattr(self, attr)
            if buf is not None:
                buf.free()
                setattr(self, attr, None)

        for i in range(2):
            if self._d_results[i] is not None:
                self._d_results[i].free()
                self._d_results[i] = None
            self._result_hosts[i] = None
            self._streams[i] = None

    def _setup_cuda(self) -> None:
        """Compile the CUDA kernel and upload the SHA-256 round-constant table."""
        if not self.KERNEL_PATH.exists():
            raise FileNotFoundError(f"CUDA kernel source not found: {self.KERNEL_PATH}")

        kernel_source = self.KERNEL_PATH.read_text(encoding="utf-8")
        options = ["-O3", f"--gpu-architecture={self._gpu_arch}", "--ptxas-options=-v"]
        module = SourceModule(kernel_source, options=options, no_extern_c=False)

        dev_k_sym, _ = module.get_global("dev_k")
        cuda.memcpy_htod(dev_k_sym, self.K_CONST_HOST)
        self._miner_kernel = module.get_function("miner_kernel")

        self._alloc_device_buffers()

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prepend_env_path_if_not_set(env_variable_name: str, path_to_prepend: str) -> None:
        """Prepend a path to an environment variable if not already present."""
        current = os.environ.get(env_variable_name, "")
        existing = [p for p in current.split(os.pathsep) if p]
        if path_to_prepend not in existing:
            new_val = f"{path_to_prepend}{os.pathsep}{current}" if current else path_to_prepend
            os.environ[env_variable_name] = new_val
            logger.debug("Updated %s: prepended '%s'", env_variable_name, path_to_prepend)
        else:
            logger.debug("%s: '%s' already present.", env_variable_name, path_to_prepend)

    @staticmethod
    def _string_to_bytes(string: str) -> bytes:
        try:
            return bytes.fromhex(string)
        except ValueError:
            return base58.b58decode(string)

    @staticmethod
    def _timestamp() -> int:
        return int(time.time())

    @staticmethod
    def _build_prefix(
        last_block_hash_hex: str,
        address_bytes: bytes,
        merkle_root_hex: str,
        difficulty: float,
        timestamp: Optional[int] = None,
    ) -> bytes:
        """Build the constant block prefix (everything except the nonce).

        Args:
            last_block_hash_hex: Hex-encoded hash of the previous block.
            address_bytes: Decoded mining-reward address bytes.
            merkle_root_hex: Hex-encoded Merkle root of pending transactions.
            difficulty: Current mining difficulty.
            timestamp: UNIX timestamp to embed.  Defaults to the current time
                when None.  Pass an explicit value for deterministic testing.
        """
        if timestamp is None:
            timestamp = Miner._timestamp()

        last_block_hash = bytes.fromhex(last_block_hash_hex)
        merkle_root = bytes.fromhex(merkle_root_hex)
        difficulty_scaled = int(difficulty * 10).to_bytes(2, 'little')
        base = (
            last_block_hash +
            address_bytes +
            merkle_root +
            timestamp.to_bytes(4, 'little') +
            difficulty_scaled
        )
        if len(address_bytes) == 33:
            base = (2).to_bytes(1, 'little') + base
        return base

    @staticmethod
    def _compute_fractional_charset(difficulty: float) -> Tuple[int, str]:
        """Return (idiff, allowed_charset_upper) for the current difficulty."""
        decimal = difficulty % 1
        idiff = int(difficulty)
        if decimal > 0:
            count = ceil(16 * (1 - decimal))
            allowed = '0123456789ABCDEF'[:count]
        else:
            allowed = '0123456789ABCDEF'
        return idiff, allowed

    @staticmethod
    def _make_last_block_chunk(last_block_hash_hex: str, idiff: int) -> str:
        """Return the last idiff hex nibbles of last_block_hash in uppercase."""
        chunk = last_block_hash_hex[-idiff:] if idiff > 0 else ''
        return chunk.upper()

    # ------------------------------------------------------------------
    # Block submission
    # ------------------------------------------------------------------

    def _submit_block(self, last_block_id: int, txs, block_content: bytes) -> int:
        """POST a candidate block to the node and return a STATUS_* code."""
        try:
            payload = {
                'block_content': block_content.hex(),
                'txs': txs,
                'id': last_block_id + 1
            }
            timeout = 20 + int((len(txs) or 1) / 3)
            r = requests.post(f"{self._node_url}push_block", json=payload, timeout=timeout)

            try:
                response = r.json()
                if 'result' in response:
                    logger.info("Node Response: %s\n", response['result'])
                elif 'error' in response:
                    logger.info("Node Response: %s\n", response['error'])
                else:
                    logger.info("Node Response: %s\n", response)
            except ValueError:
                logger.info("Node Response: %s (HTTP %s)\n", r.text, r.status_code)
                return STATUS_FAILED

            if r.status_code >= 400:
                status_field = response.get('status', 'unknown')
                if status_field in ('sync', 'stale'):
                    return STATUS_STALE
                elif status_field == 'failed':
                    return STATUS_FAILED
                elif r.status_code == 503:
                    return STATUS_STALE
                return STATUS_FAILED

            r.raise_for_status()

            status_field = response.get('status')
            ok_value = response.get('ok', False)
            result_message = response.get('result', '')

            if status_field is not None:
                if status_field == 'success' and ok_value is True:
                    return STATUS_SUCCESS
                elif status_field in ('stale', 'sync'):
                    return STATUS_STALE
                elif status_field == 'failed':
                    return STATUS_FAILED

            if ok_value is True and r.status_code == 200:
                if isinstance(result_message, str):
                    rl = result_message.lower()
                    if 'accepted' in rl or 'success' in rl:
                        return STATUS_SUCCESS
                    elif 'stale' in rl:
                        return STATUS_STALE
                    elif 'failed' in rl or 'error' in rl:
                        return STATUS_FAILED
                return STATUS_SUCCESS

            logger.warning("Could not determine submission status from response: %s\n", response)
            return STATUS_FAILED

        except requests.exceptions.RequestException as e:
            logger.error("Error submitting block (network/node issue): %s\n", e)
            return STATUS_FAILED

    # ------------------------------------------------------------------
    # Main mining loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Execute the main mining loop."""

        if threading.current_thread() is not threading.main_thread():
            pycuda.autoinit.context.push()
            self._context_pushed = True

        self._stats.start_session()
        self._start_polling_listener()
        self._setup_cuda()

        miner_kernel = self._miner_kernel
        assert miner_kernel is not None, "CUDA kernel not initialised"

        d_prefix = self._d_prefix
        d_last_chunk = self._d_last_chunk
        d_charset = self._d_charset

        assert d_prefix is not None and d_last_chunk is not None and d_charset is not None
        assert self._d_results[0] is not None and self._d_results[1] is not None
        assert self._result_hosts[0] is not None and self._result_hosts[1] is not None
        assert self._streams[0] is not None and self._streams[1] is not None

        nonces_per_batch = self._nonces_per_batch
        max_batches = self._max_batches
        global_step_int = self._gpu_blocks * self._gpu_threads
        global_step_u32 = np.uint32(global_step_int)
        iters_u32 = np.uint32(self._gpu_iterations)
        start_offset_u32 = np.uint32(0)

        try:
            while True:
                if self._stop_event and self._stop_event.is_set():
                    break

                # ---- Fetch mining info with exponential backoff ----
                mining_info = None
                backoff = BACKOFF_BASE_SECONDS
                while mining_info is None:
                    if self._stop_event and self._stop_event.is_set():
                        return
                    try:
                        logger.info("Fetching fresh mining info from node...")
                        resp = requests.get(f"{self._node_url}get_mining_info", timeout=10)
                        resp.raise_for_status()
                        mining_info = resp.json().get('result')
                        if not mining_info:
                            raise ValueError("Node response did not contain 'result' data.")
                    except (requests.exceptions.RequestException, ValueError) as exc:
                        logger.error("%s — retrying in %.1fs...\n", exc, backoff)
                        wait = backoff
                        backoff = min(backoff * 2.0, BACKOFF_MAX_SECONDS)
                        if self._stop_event:
                            if self._stop_event.wait(wait):
                                return
                        else:
                            time.sleep(wait)

                difficulty = mining_info['difficulty']
                last_block = mining_info['last_block']
                last_block_hash_hex = last_block.get('hash', (33_554_432).to_bytes(32, 'little').hex())
                last_block_id = last_block.get('id', 0)
                txs = mining_info['pending_transactions_hashes']
                merkle_root_hex = mining_info['merkle_root']

                logger.info("Difficulty: %s", difficulty)
                logger.info("Current Block Height: %s", last_block_id)
                logger.info(f"Pending Transaction Count: {len(txs)}\n")
                logger.debug("Using Merkle Root provided by node: %s\n", merkle_root_hex)

                target_height = last_block_id + 1
                self._prepare_for_new_block(target_height)
                self._stats.start_block(target_height, difficulty)
                self._stats.update_status("Hashing")

                # Capture timestamp explicitly so it is fresh per outer-loop
                # iteration and so the call is deterministically testable.
                ts = self._timestamp()
                prefix_bytes = self._build_prefix(
                    last_block_hash_hex,
                    self._address_bytes,
                    merkle_root_hex,
                    difficulty,
                    timestamp=ts,
                )
                if len(prefix_bytes) > self._MAX_PREFIX_BYTES:
                    raise ValueError(
                        f"Prefix length {len(prefix_bytes)} exceeds max {self._MAX_PREFIX_BYTES}"
                    )

                idiff, allowed_charset = self._compute_fractional_charset(difficulty)
                last_chunk_uc = self._make_last_block_chunk(last_block_hash_hex, idiff)

                # Upload per-block constants once — not inside the inner loop.
                cuda.memcpy_htod(d_prefix, prefix_bytes)
                if idiff > 0:
                    if idiff > self._MAX_LAST_CHUNK_BYTES:
                        raise ValueError(
                            f"idiff {idiff} exceeds max last_chunk bytes {self._MAX_LAST_CHUNK_BYTES}"
                        )
                    cuda.memcpy_htod(d_last_chunk, last_chunk_uc.encode('ascii'))

                charset_bytes = allowed_charset.encode('ascii')
                if len(charset_bytes) > self._MAX_CHARSET_BYTES:
                    raise ValueError(
                        f"charset {len(charset_bytes)}B exceeds max {self._MAX_CHARSET_BYTES}B"
                    )
                cuda.memcpy_htod(d_charset, charset_bytes)

                # Initialise both double-buffer result slots to the sentinel
                # once per block.  The kernel writes a found nonce into the
                # buffer atomically; a sentinel value means "not found yet".
                for i in range(2):
                    self._result_hosts[i][0] = np.uint32(0xFFFFFFFF)
                    cuda.memcpy_htod(self._d_results[i], self._result_hosts[i])

                # ---- Double-buffered GPU inner loop -------------------
                #
                # Per iteration N:
                #   1. Launch batch N on streams[cur] (fully async).
                #   2. Queue an async DtoH for result[cur] in the same stream.
                #   3. While the GPU runs batch N, evaluate stop conditions and
                #      synchronise streams[prev] to read batch N-1's result.
                #   4. On solution or a stop condition: drain the current stream
                #      and break.
                #
                # Hash accounting: stats.add_hashes() is called only after the
                # corresponding stream has been synchronised (work confirmed
                # complete).  A running tally (batches_counted) prevents either
                # double-counting or missed batches when breaking mid-loop.
                # -------------------------------------------------------
                logger.info("Attempting to mine block: %s", last_block_id + 1)
                block_start_time = time.time()
                batch_idx = 0
                found_nonce: Optional[int] = None
                nonce_exhausted = False
                batches_counted = 0   # how many batches have had hashes credited

                while True:
                    if self._stop_event and self._stop_event.is_set():
                        self._streams[batch_idx & 1].synchronize()
                        break

                    cur = batch_idx & 1

                    base_offset_u32 = np.uint32(
                        ((batch_idx * self._gpu_iterations) * global_step_int) & 0xFFFFFFFF
                    )

                    # --- Launch kernel async on current stream ---
                    miner_kernel(
                        d_prefix,
                        np.uint64(len(prefix_bytes)),
                        d_last_chunk,
                        np.uint32(idiff),
                        d_charset,
                        np.uint32(len(charset_bytes)),
                        self._d_results[cur],
                        start_offset_u32,
                        global_step_u32,
                        base_offset_u32,
                        iters_u32,
                        block=(self._gpu_threads, 1, 1),
                        grid=(self._gpu_blocks, 1),
                        stream=self._streams[cur],
                    )
                    # Queue async DtoH — runs after the kernel in the same stream.
                    cuda.memcpy_dtoh_async(
                        self._result_hosts[cur], self._d_results[cur], self._streams[cur]
                    )

                    # --- Evaluate stop conditions while GPU runs ---
                    nonce_exhausted = (batch_idx + 1) >= max_batches
                    time_expired = (time.time() - block_start_time) >= WORKER_REFRESH_SECONDS
                    refresh_req = self._block_refresh_event.is_set()

                    # --- Sync previous stream and check its result ---
                    # This overlaps with the GPU executing the current batch.
                    if batch_idx > 0:
                        prev = 1 - cur
                        self._streams[prev].synchronize()
                        self._stats.add_hashes(nonces_per_batch)
                        batches_counted += 1

                        if self._result_hosts[prev][0] != np.uint32(0xFFFFFFFF):
                            found_nonce = int(self._result_hosts[prev][0])
                            # Drain the still-running current stream too.
                            self._streams[cur].synchronize()
                            self._stats.add_hashes(nonces_per_batch)
                            batches_counted += 1
                            break

                    # --- Handle stop conditions ---
                    if nonce_exhausted or time_expired or refresh_req:
                        self._streams[cur].synchronize()
                        # Credit hashes for the current batch if not yet done.
                        if batches_counted <= batch_idx:
                            self._stats.add_hashes(nonces_per_batch)
                            batches_counted += 1
                        # Check current batch for a last-chance solution.
                        if self._result_hosts[cur][0] != np.uint32(0xFFFFFFFF):
                            found_nonce = int(self._result_hosts[cur][0])
                        if nonce_exhausted and found_nonce is None:
                            logger.info("Full 32-bit nonce space exhausted.\n")
                        break

                    batch_idx += 1

                # ---- End inner loop -----------------------------------

                if self._stop_event and self._stop_event.is_set():
                    self._clear_current_block_target()
                    break

                if self._block_refresh_event.is_set():
                    self._stats.update_status("New block detected")
                    logger.debug("Chain advanced during batch; refreshing work.")
                    self._clear_current_block_target()
                    continue

                if found_nonce is None:
                    if nonce_exhausted:
                        self._stats.update_status("Nonce space exhausted, refreshing")
                    else:
                        self._stats.update_status("Window expired, refreshing")
                    logger.debug("No solution in this window. Refreshing mining info...")
                    continue

                block_content = prefix_bytes + int(found_nonce).to_bytes(4, 'little')
                logger.info("Potential block found! Submitting to node...")
                logger.debug("Block Content: %s", block_content.hex())
                if txs:
                    logger.debug("Transactions: %s", ','.join(txs))

                if self._block_refresh_event.is_set():
                    latest_height = (
                        self._latest_network_block_id
                        if self._latest_network_block_id is not None
                        else "unknown"
                    )
                    self._stats.update_status("New block detected")
                    logger.info(
                        "Aborting submission — network advanced to height %s.", latest_height
                    )
                    self._clear_current_block_target()
                    continue

                self._stats.update_status("Submitting block")
                status = self._submit_block(last_block_id, txs, block_content)

                if status == STATUS_SUCCESS:
                    self._stats.increment_blocks_mined()
                    self._stats.update_status("Accepted")
                    if len(txs) > 0:
                        logger.info(f"Confirmed {len(txs)} transactions: {len(txs)}\n")

                    max_blocks_str = self._max_blocks if self._max_blocks is not None else '∞'
                    logger.info("Total blocks mined: %s / %s", self._stats.blocks_mined, max_blocks_str)
                    if self._max_blocks is not None and self._stats.blocks_mined >= self._max_blocks:
                        logger.info("Reached max blocks (%d). Exiting.", self._max_blocks)
                        self._clear_current_block_target()
                        break
                    logger.info("Preparing for next block...\n")
                    if self._stop_event:
                        if self._stop_event.wait(2):
                            self._clear_current_block_target()
                            break
                    else:
                        time.sleep(2)

                elif status == STATUS_STALE:
                    self._stats.update_status("Stale block")
                    logger.info("Block was stale. Restarting with fresh data...\n")
                    self._clear_current_block_target()
                    if self._stop_event:
                        if self._stop_event.wait(2):
                            break
                    else:
                        time.sleep(2)

                else:
                    self._stats.update_status("Submission failed")
                    logger.warning("Block submission failed. Restarting with fresh data...\n")
                    self._clear_current_block_target()
                    if self._stop_event:
                        if self._stop_event.wait(2):
                            break
                    else:
                        time.sleep(2)

            logger.info("Mining complete. Press Ctrl+C to exit.")
            self._stats.update_status("Mining complete. Press Ctrl+C to exit.")

        finally:
            self._shutdown_polling_listener()
            self._clear_current_block_target()
            self._stats.finish_session()
            self._free_device_buffers()
            if self._context_pushed:
                pycuda.autoinit.context.pop()


class ConsoleExitHandler:
    """Handles graceful exit prompts for console mode."""

    @staticmethod
    def wait_for_ctrl_c(message: str = "Press Ctrl+C to exit.") -> None:
        """Block until the user presses Ctrl+C."""
        logger.info(message)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass


def main() -> None:
    """Entry point: parse arguments, configure logging, and start the miner."""

    parser = argparse.ArgumentParser(
        description="Single-worker CUDA miner for Denaro (PyCUDA JIT; RTX 4090 defaults).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--address', '-a', required=True,
                        help="Mining address to receive block rewards.")
    parser.add_argument('--node', '-n', default=DEFAULT_NODE_URL,
                        help="URL of the Denaro node.")
    parser.add_argument('--max-blocks', '-m', type=int, default=None,
                        help="Max number of blocks to mine before exit. Runs indefinitely when not specified.")
    parser.add_argument('--gpu-blocks', dest="gpu_blocks", type=int, default=256,
                        help="CUDA grid blocks per kernel launch.")
    parser.add_argument('--gpu-threads', dest="gpu_threads", type=int, default=256,
                        help="CUDA threads per block.")
    parser.add_argument('--gpu-iterations', dest="gpu_iterations", type=int, default=10000,
                        help="Iterations per thread per kernel batch.")
    parser.add_argument('--gpu-arch', dest="gpu_arch", required=True,
                        help="nvcc --gpu-architecture target "
                             "(see https://arnon.dk/matching-sm-architectures-arch-and-gencode-for-various-nvidia-cards/).")
    parser.add_argument('--verbose', '-v', action='store_true',
                        help="Enables DEBUG-level logging.")
    parser.add_argument('--no-tui', action='store_true',
                        help="Disables the Textual TUI; print logs to stdout instead.")

    args = parser.parse_args()

    use_tui = not args.no_tui
    log_level = logging.DEBUG if args.verbose else logging.INFO

    log_queue: Optional[Queue]
    stats: Optional[MiningStats]
    stop_event: Optional[threading.Event]

    if use_tui:
        log_queue = Queue(maxsize=512)
        stats = MiningStats(max_blocks=args.max_blocks)
        stop_event = threading.Event()
        handler = TUILogHandler(log_queue, formatter=logging.Formatter('%(levelname)s - %(message)s'))
        logging.basicConfig(level=log_level, handlers=[handler])
    else:
        log_queue = None
        stats = None
        stop_event = None
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S',
        )

    for noisy_lib in ("requests", "urllib3"):
        lib_logger = logging.getLogger(noisy_lib)
        lib_logger.setLevel(logging.WARNING)
        lib_logger.propagate = False

    Miner._prepend_env_path_if_not_set("PATH", "/usr/local/cuda/bin")
    Miner._prepend_env_path_if_not_set("LD_LIBRARY_PATH", "/usr/local/cuda/lib64")

    node_url = args.node
    if not node_url.endswith('/'):
        node_url += '/'

    logger.info("Starting CUDA miner (single worker) for address: %s", args.address)
    logger.info("Connecting to node: %s", node_url)
    logger.debug(
        "GPU launch dims: blocks=%d, threads=%d, iters_per_thread=%d",
        args.gpu_blocks, args.gpu_threads, args.gpu_iterations,
    )
    logger.debug("GPU architecture flag: %s\n", args.gpu_arch)
    if args.max_blocks is not None:
        logger.info("Will stop after mining %d block(s).\n", args.max_blocks)

    miner = Miner(
        address=args.address,
        node_url=node_url,
        gpu_blocks=args.gpu_blocks,
        gpu_threads=args.gpu_threads,
        gpu_iterations=args.gpu_iterations,
        gpu_arch=args.gpu_arch,
        max_blocks=args.max_blocks,
        stats=stats if use_tui else None,
        stop_event=stop_event if use_tui else None,
    )

    if use_tui and log_queue and stats and stop_event:
        mining_thread = threading.Thread(target=miner.run, daemon=True)
        mining_thread.start()
        try:
            MinerTUI(log_queue, stats, stop_event).run()
        finally:
            stop_event.set()
            mining_thread.join()
    else:
        miner.run()
        ConsoleExitHandler.wait_for_ctrl_c()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        ConsoleExitHandler.wait_for_ctrl_c("Shutdown requested. Press Ctrl+C again to exit.")
        sys.exit(0)
    except Exception as exc:  # pragma: no cover
        logger.exception("Fatal error: %s", exc)
        ConsoleExitHandler.wait_for_ctrl_c("Fatal error encountered. Press Ctrl+C to exit.")
        sys.exit(1)
