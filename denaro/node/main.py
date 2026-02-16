# denaro/node/main.py - SECURE IMPLEMENTATION
import random
import asyncio
from asyncio import gather, Lock
from collections import deque, defaultdict
import os
import re
import json
from decimal import Decimal
from datetime import datetime, timedelta
import hashlib
import hmac
import traceback
import time
from typing import Optional, Set, Dict, List, Tuple, Any
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import ipaddress
import socket
from urllib.parse import urlparse

from asyncpg import UniqueViolationError
from fastapi import FastAPI, Body, Query, Depends, HTTPException, status 
from fastapi.responses import RedirectResponse, Response

import httpx
from httpx import TimeoutException
from icecream import ic
from starlette.background import BackgroundTasks, BackgroundTask
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from denaro.helpers import timestamp, sha256, transaction_to_json
from denaro.logger import get_logger

from denaro.manager import (
    create_block, get_difficulty, Manager, get_transactions_merkle_tree, 
    calculate_difficulty, clear_pending_transactions, block_to_bytes, split_block_content
)

from denaro.node.nodes_manager import NodesManager, NodeInterface
from denaro.transactions import Transaction, CoinbaseTransaction
from denaro import Database
from denaro.constants import (
    MAX_MINING_CANDIDATES, NODE_VERSION, MAX_BLOCKS_PER_SUBMISSION,
    MAX_BLOCK_CONTENT_SIZE, MAX_PEERS, MAX_CONCURRENT_SYNCS,
    MAX_TX_FETCH_LIMIT, MAX_MEMPOOL_SIZE, CONNECTION_TIMEOUT,
    MAX_BATCH_BYTES, VALID_HEX_PATTERN, VALID_ADDRESS_PATTERN,
    DENARO_BOOTSTRAP_NODE, DENARO_SELF_URL, POSTGRES_USER,
    POSTGRES_PASSWORD, DENARO_DATABASE_NAME, DENARO_DATABASE_HOST,
    MAX_TX_DATA_SIZE, DENARO_NODE_HOST, DENARO_NODE_PORT, MAX_REORG_DEPTH,
    LOG_INCLUDE_REQUEST_CONTENT, LOG_INCLUDE_RESPONSE_CONTENT, LOG_INCLUDE_BLOCK_SYNC_MESSAGES,
    LOG_MAX_PATH_LENGTH, DENARO_DATA_DIR
)
from denaro.node.identity import (
    initialize_identity, get_node_id, get_public_key_hex, 
    verify_signature, get_canonical_json_bytes, sign_message
)

logger = get_logger(__name__)

# ============================================================================
# SECURITY COMPONENTS
# ============================================================================

class TimeBasedCache:
    """Thread-safe cache with automatic expiration"""
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._access_order = deque(maxlen=max_size)
        self._lock = asyncio.Lock()
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        
    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl_seconds:
                    return value
                else:
                    del self._cache[key]
            return None
    

    async def put(self, key: str, value: Any):
        async with self._lock:
            current_time = time.time()
            
            # If the cache is full and we're adding a new key, we need to make space.
            # This loop handles both size enforcement and cleaning of old items in one pass.
            while len(self._cache) >= self.max_size and key not in self._cache:
                if not self._access_order:
                    # Should not happen if cache is not empty, but a safeguard.
                    break
                
                # Get the oldest key from the access queue
                oldest_key = self._access_order.popleft()
                
                # If this key is still in the cache, remove it.
                # This check resolves the race condition, as the key might have
                # been removed by another operation or expired.
                if oldest_key in self._cache:
                    del self._cache[oldest_key]
            
            # Now that there is space, add the new item.
            self._cache[key] = (value, current_time)
            self._access_order.append(key)

    async def contains(self, key: str) -> bool:
        return await self.get(key) is not None
    
    async def clean(self):
        """Manual cleanup of expired entries"""
        async with self._lock:
            current_time = time.time()
            expired_keys = [
                k for k, (_, ts) in self._cache.items() 
                if current_time - ts >= self.ttl_seconds
            ]
            for k in expired_keys:
                del self._cache[k]


class HandshakeChallengeManager:
    """Secure challenge management with automatic cleanup"""
    def __init__(self, ttl_seconds: int = 300):
        self._challenges: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.ttl_seconds = ttl_seconds
        self._cleanup_task = None
        
    async def start(self):
        """Start periodic cleanup task"""
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        
    async def stop(self):
        """Stop cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            
    async def _periodic_cleanup(self):
        """Remove expired challenges every 60 seconds"""
        while True:
            await asyncio.sleep(60)
            await self.cleanup()
            
    async def cleanup(self):
        """Remove expired challenges"""
        async with self._lock:
            current_time = time.time()
            expired = [
                challenge for challenge, timestamp in self._challenges.items()
                if current_time - timestamp > self.ttl_seconds
            ]
            for challenge in expired:
                del self._challenges[challenge]
                
    async def create_challenge(self) -> str:
        """Create a new challenge"""
        challenge = os.urandom(32).hex()
        
        async with self._lock:
            # Prevent unlimited growth
            if len(self._challenges) > 10000:
                # Remove oldest half
                sorted_challenges = sorted(self._challenges.items(), key=lambda x: x[1])
                for challenge_to_remove, _ in sorted_challenges[:5000]:
                    del self._challenges[challenge_to_remove]
                    
            self._challenges[challenge] = time.time()
            
        return challenge
        
    async def verify_and_consume_challenge(self, challenge: str) -> bool:
        """Verify challenge exists and immediately consume it"""
        async with self._lock:
            if challenge in self._challenges:
                timestamp = self._challenges[challenge]
                current_time = time.time()
                
                # Check if expired
                if current_time - timestamp > self.ttl_seconds:
                    del self._challenges[challenge]
                    return False
                    
                # Valid challenge - consume it immediately
                del self._challenges[challenge]
                return True
                
            return False


class BoundedPeerSyncTracker:
    """Track peer sync operations with size limits"""
    def __init__(self, max_peers: int = MAX_PEERS):
        self._peers_in_sync: Set[str] = set()
        self._sync_timestamps: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.max_peers = max_peers
        
    async def add_peer(self, peer_id: str) -> bool:
        """Add peer to sync set if not at limit"""
        async with self._lock:
            if peer_id in self._peers_in_sync:
                return False
                
            if len(self._peers_in_sync) >= self.max_peers:
                # Remove oldest peer
                if self._sync_timestamps:
                    oldest_peer = min(self._sync_timestamps.items(), key=lambda x: x[1])[0]
                    self._peers_in_sync.discard(oldest_peer)
                    del self._sync_timestamps[oldest_peer]
                    
            self._peers_in_sync.add(peer_id)
            self._sync_timestamps[peer_id] = time.time()
            return True
            
    async def remove_peer(self, peer_id: str):
        """Remove peer from sync set"""
        async with self._lock:
            self._peers_in_sync.discard(peer_id)
            self._sync_timestamps.pop(peer_id, None)
            
    async def is_syncing(self, peer_id: str) -> bool:
        """Check if peer is currently syncing"""
        async with self._lock:
            return peer_id in self._peers_in_sync


class SyncStateManager:
    """Thread-safe synchronization state management"""
    def __init__(self, max_concurrent_syncs: int = MAX_CONCURRENT_SYNCS):
        self.is_syncing = False
        self.active_sync_count = 0
        self.max_concurrent_syncs = max_concurrent_syncs
        self._sync_lock = asyncio.Lock()
        self._count_lock = asyncio.Lock()
        
    @asynccontextmanager
    async def acquire_sync(self):
        """Context manager for sync operations"""
        acquired = False
        try:
            async with self._sync_lock:
                if self.is_syncing:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Node is already synchronizing"
                    )
                    
                async with self._count_lock:
                    if self.active_sync_count >= self.max_concurrent_syncs:
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Maximum concurrent syncs reached"
                        )
                    
                    self.is_syncing = True
                    self.active_sync_count += 1
                    acquired = True
                    
            yield
            
        finally:
            if acquired:
                async with self._sync_lock:
                    self.is_syncing = False
                async with self._count_lock:
                    self.active_sync_count = max(0, self.active_sync_count - 1)


class InputValidator:
    """Comprehensive input validation"""
    
    @staticmethod
    def validate_hex(hex_string: str, min_length: int = 1, max_length: int = None) -> bool:
        """Validate hex string format and length"""
        if not hex_string:
            return False
            
        if max_length and len(hex_string) > max_length:
            return False
            
        if len(hex_string) < min_length:
            return False
            
        try:
            # Ensure even length
            if len(hex_string) % 2 != 0:
                return False
                
            # Try to decode
            bytes.fromhex(hex_string)
            return True
        except ValueError:
            return False
            
    @staticmethod
    async def validate_block_height(height: int, db, max_ahead: int = 10) -> bool:
        """Validate block height is reasonable"""
        if height < 0:
            return False
            
        current_height = await db.get_next_block_id() - 1
        
        # Don't accept blocks too far in the future
        if height > current_height + max_ahead:
            return False
            
        return True
        
    @staticmethod
    def validate_address(address: str) -> bool:
        """Validate address format using canonical pattern"""
        if not address:
            return False

        if len(address) < 40 or len(address) > 128:
            return False

        return bool(VALID_ADDRESS_PATTERN.match(address))
        
    @staticmethod
    def validate_transaction_data(tx_hex: str) -> Tuple[bool, Optional[str]]:
        """Comprehensive transaction validation"""
        if not tx_hex:
            return False, "Empty transaction"
            
        if len(tx_hex) > 2_075_000:
            return False, "Transaction too large"
            
        if not InputValidator.validate_hex(tx_hex):
            return False, "Invalid hex format"
            
        # Additional validation could go here
        return True, None


class AuthenticatedRequestValidator:
    """Validate request signatures with timestamp checks"""
    
    def __init__(self, max_age_seconds: int = 300):
        self.max_age_seconds = max_age_seconds
        self._nonce_cache = TimeBasedCache(max_size=10000, ttl_seconds=max_age_seconds)
        
    async def validate_request(self, request: Request) -> Optional[str]:
        """
        Validates the request signature, timestamp, and nonce.
        Returns the verified node_id on success, or None on failure.
        """
        node_id = request.headers.get('x-node-id')
        pubkey = request.headers.get('x-public-key')
        signature = request.headers.get('x-signature')
        timestamp_header = request.headers.get('x-timestamp')
        nonce = request.headers.get('x-nonce')
        
        if not all([node_id, pubkey, signature, timestamp_header, nonce]):
            return None
            
        # 1. Validate timestamp
        try:
            request_time = int(timestamp_header)
            current_time = int(time.time())
            if abs(current_time - request_time) > self.max_age_seconds:
                return None
        except (ValueError, TypeError):
            return None
            
        # 2. Check nonce for replay attacks
        nonce_key = f"{node_id}:{nonce}"
        if await self._nonce_cache.contains(nonce_key):
            return None
        
        # 3. Verify the cryptographic signature
        try:
            request_body_bytes = await request.body()
            
            # Start with the base payload
            payload_to_verify = {
                "body": request_body_bytes.decode('utf-8'),
                "timestamp": request_time,
                "nonce": nonce
            }

            # Reconstruct the full payload by looking for our custom signed headers.
            # The client signed these, so we MUST include them for verification.
            for key, value in request.headers.items():
                if key.lower().startswith('x-denaro-'):
                    # The client added this key without the prefix to the signed dict.
                    # e.g., 'x-denaro-height' -> 'height'
                    original_key = key.lower().replace('x-denaro-', '')
                    # Attempt to convert numeric values back to numbers for a perfect match
                    try:
                        # Check if it looks like a number (int or float)
                        if '.' in value:
                             payload_to_verify[original_key] = float(value)
                        else:
                             payload_to_verify[original_key] = int(value)
                    except ValueError:
                        # It's not a number, treat it as a string.
                        # Handle 'None' string specifically.
                        if value == 'None':
                            payload_to_verify[original_key] = None
                        else:
                            payload_to_verify[original_key] = value
            
            canonical_bytes = get_canonical_json_bytes(payload_to_verify)
            
            if not verify_signature(pubkey, signature, canonical_bytes):
                # The reconstructed signature does not match.
                logger.error(f"Signature verification failed for peer {node_id}.")
                return None

        except Exception as e:
            logger.error(f"Error during signature validation: {e}")
            return None

        # All checks passed. Store the nonce and return the verified node_id.
        await self._nonce_cache.put(nonce_key, True)
        
        return node_id


class DNSSafeHTTPClient:
    """HTTP client with DNS rebinding protection"""
    
    def __init__(self, timeout: float = 10.0):
        self._dns_cache: Dict[str, Tuple[str, float]] = {}
        self._cache_ttl = 300  # 5 minutes
        self._lock = asyncio.Lock()
        self.timeout = timeout
        
    async def validate_and_resolve(self, url: str) -> Tuple[bool, Optional[str]]:
        """Validate URL and resolve with caching"""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ['http', 'https']:
                return False, None
                
            hostname = parsed.hostname
            if not hostname:
                return False, None
                
            async with self._lock:
                # Check cache
                if hostname in self._dns_cache:
                    cached_ip, cache_time = self._dns_cache[hostname]
                    if time.time() - cache_time < self._cache_ttl:
                        return True, cached_ip
                        
                # Resolve
                loop = asyncio.get_event_loop()
                addr_info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
                resolved_ip = addr_info[0][4][0]
                
                # Validate IP
                ip_obj = ipaddress.ip_address(resolved_ip)
                if not ip_obj.is_global and not ip_obj.is_private:
                    return False, None
                    
                # Cache resolution
                self._dns_cache[hostname] = (resolved_ip, time.time())
                
                return True, resolved_ip
                
        except Exception:
            return False, None


@dataclass
class PeerViolation:
    timestamp: float
    violation_type: str
    severity: int  # 1-10
    details: Optional[str] = None


class PeerReputationManager:
    """Track peer behavior and ban malicious peers"""
    
    def __init__(self, ban_threshold: int = -100, violation_ttl: int = 86400):
        self._peer_scores: Dict[str, int] = defaultdict(int)
        self._violations: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._banned_peers: Set[str] = set()
        self._lock = asyncio.Lock()
        self.ban_threshold = ban_threshold
        self.violation_ttl = violation_ttl
        
    async def record_violation(self, peer_id: str, violation_type: str, severity: int = 5, details: str = None):
        """Record a violation and update peer score"""
        async with self._lock:
            violation = PeerViolation(
                timestamp=time.time(),
                violation_type=violation_type,
                severity=severity,
                details=details
            )
            
            self._violations[peer_id].append(violation)
            
            # Update score
            score_penalty = severity * 10
            self._peer_scores[peer_id] -= score_penalty

            # Clamp score to ban_threshold to prevent unbounded negative scores
            self._peer_scores[peer_id] = max(self._peer_scores[peer_id], self.ban_threshold)
            
            # Check if peer should be banned
            if self._peer_scores[peer_id] <= self.ban_threshold:
                self._banned_peers.add(peer_id)
                
    async def record_good_behavior(self, peer_id: str, points: int = 1):
        """Reward good behavior"""
        async with self._lock:
            self._peer_scores[peer_id] = min(100, self._peer_scores[peer_id] + points)
            
    async def is_banned(self, peer_id: str) -> bool:
        """Check if peer is banned"""
        async with self._lock:
            return peer_id in self._banned_peers
            
    async def get_score(self, peer_id: str) -> int:
        """Get current peer score"""
        async with self._lock:
            return self._peer_scores.get(peer_id, 0)
            
    async def cleanup_old_violations(self):
        """Remove old violations"""
        async with self._lock:
            current_time = time.time()
            
            for peer_id, violations in list(self._violations.items()):
                # Remove old violations
                while violations and current_time - violations[0].timestamp > self.violation_ttl:
                    violations.popleft()
                    
                # Remove peer data if no violations
                if not violations and peer_id not in self._banned_peers:
                    del self._violations[peer_id]
                    if peer_id in self._peer_scores and self._peer_scores[peer_id] >= 0:
                        del self._peer_scores[peer_id]
                
                # Recalculate score from remaining violations and unban
                elif violations:
                    recalculated = -sum(v.severity * 10 for v in violations)
                    recalculated = max(recalculated, self.ban_threshold)
                    self._peer_scores[peer_id] = recalculated
                    if peer_id in self._banned_peers and recalculated > self.ban_threshold:
                        self._banned_peers.discard(peer_id)
                        logger.info(f"Peer {peer_id} unbanned after violations expired (score: {recalculated})")


class QueryCostCalculator:
    """Calculate and limit database query costs"""
    
    def __init__(self, max_cost_per_hour: int = 1000):
        self._costs: Dict[str, float] = defaultdict(float)
        self._reset_times: Dict[str, float] = defaultdict(time.time)
        self._lock = asyncio.Lock()
        self.max_cost_per_hour = max_cost_per_hour
        
    async def check_and_update_cost(self, identifier: str, offset: int, limit: int):
        """Check if query is allowed and update cost"""
        async with self._lock:
            current_time = time.time()
            
            # Reset if hour has passed
            if current_time - self._reset_times[identifier] > 3600:
                self._costs[identifier] = 0
                self._reset_times[identifier] = current_time
                
            # Calculate cost (higher offset = higher cost)
            cost = (offset / 100) + (limit / 50)
            
            if self._costs[identifier] + cost > self.max_cost_per_hour:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Query cost limit exceeded. Try again later."
                )
                
            self._costs[identifier] += cost


class SecurityMonitor:
    """Monitor and log security events"""
    
    def __init__(self):
        self._metrics = {
            'failed_validations': defaultdict(int),
            'rate_limit_hits': defaultdict(int),
            'banned_peers': 0,
            'replay_attempts': 0,
            'dns_rebinding_attempts': 0,
            'resource_exhaustion_attempts': 0
        }
        self._lock = asyncio.Lock()
        
    async def log_event(self, event_type: str, details: dict):
        """Log security event"""
        async with self._lock:
            # Update metrics
            if event_type in self._metrics:
                if isinstance(self._metrics[event_type], dict):
                    key = details.get('subtype', 'default')
                    self._metrics[event_type][key] += 1
                else:
                    self._metrics[event_type] += 1
                    
    async def get_metrics(self) -> dict:
        """Get current security metrics"""
        async with self._lock:
            return dict(self._metrics)
            
    async def check_thresholds(self):
        """Check if any security thresholds are exceeded"""
        async with self._lock:
            alerts = []
            
            # Check for high rate of failures
            for event_type, counts in self._metrics.items():
                if isinstance(counts, dict):
                    total = sum(counts.values())
                    if total > 1000:  # Threshold
                        alerts.append({
                            'type': event_type,
                            'count': total,
                            'severity': 'high'
                        })
                        
            return alerts


class SafeTransactionPool:
    """Thread-safe transaction pool with atomic operations"""
    
    def __init__(self, max_size: int = 10000):
        self._pool: Dict[str, Any] = {}  # tx_hash -> transaction
        self._lock = asyncio.Lock()
        self.max_size = max_size
        self._insertion_time: Dict[str, float] = {}
        
    async def add_transaction(self, tx_hash: str, transaction: Any, db) -> bool:
        """Add transaction atomically"""
        async with self._lock:
            if tx_hash in self._pool:
                return False
                
            if len(self._pool) >= self.max_size:
                # Remove oldest transactions
                sorted_txs = sorted(self._insertion_time.items(), key=lambda x: x[1])
                for old_hash, _ in sorted_txs[:self.max_size // 10]:  # Remove 10%
                    del self._pool[old_hash]
                    del self._insertion_time[old_hash]
                    
            # Add to pool
            self._pool[tx_hash] = transaction
            self._insertion_time[tx_hash] = time.time()
            
            # Add to database
            try:
                success = await db.add_pending_transaction(transaction)
                if not success:
                    # Rollback
                    del self._pool[tx_hash]
                    del self._insertion_time[tx_hash]
                    return False
                    
                return True
                
            except Exception:
                # Rollback on any error
                del self._pool[tx_hash]
                del self._insertion_time[tx_hash]
                raise
                
    async def remove_transactions(self, tx_hashes: List[str]):
        """Remove transactions atomically"""
        async with self._lock:
            for tx_hash in tx_hashes:
                self._pool.pop(tx_hash, None)
                self._insertion_time.pop(tx_hash, None)


class SecureNodeComponents:
    """Initialize all security components"""
    
    def __init__(self):
        # Caches
        self.transaction_cache = TimeBasedCache(max_size=1000, ttl_seconds=300)
        self.block_cache = TimeBasedCache(max_size=500, ttl_seconds=600)
        self.reachability_cache = TimeBasedCache(max_size=1000, ttl_seconds=300)
        
        # Managers
        self.handshake_manager = HandshakeChallengeManager()
        self.peer_sync_tracker = BoundedPeerSyncTracker(max_peers=MAX_PEERS)
        self.sync_state_manager = SyncStateManager(max_concurrent_syncs=MAX_CONCURRENT_SYNCS)
        
        # Validation
        self.input_validator = InputValidator()
        self.auth_validator = AuthenticatedRequestValidator()
        
        # Security
        self.dns_client = DNSSafeHTTPClient()
        self.reputation_manager = PeerReputationManager()
        self.query_calculator = QueryCostCalculator()
        self.security_monitor = SecurityMonitor()
        
        # Safety
        self.transaction_pool = SafeTransactionPool()
        
        # Semaphore for propagation
        self.propagation_semaphore = asyncio.Semaphore(50)
        
    async def startup(self):
        """Initialize all components"""
        await self.handshake_manager.start()
        
        # Start periodic cleanup tasks
        asyncio.create_task(self._periodic_cleanup())
        
    async def shutdown(self):
        """Cleanup all components"""
        await self.handshake_manager.stop()
        
    async def _periodic_cleanup(self):
        """Periodic cleanup of all components"""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            
            # Clean caches
            await self.transaction_cache.clean()
            await self.block_cache.clean()
            await self.reachability_cache.clean()
            
            # Clean reputation data
            await self.reputation_manager.cleanup_old_violations()
            
            # Check security thresholds
            alerts = await self.security_monitor.check_thresholds()
            if alerts:
                logger.warning(f"SECURITY ALERTS: {alerts}")


class FlagParameter(str):
    """
    Custom type for flag query parameters.
    Accepts ?param (presence) and ?param=true as True.
    Shows as boolean in OpenAPI docs.
    """
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        if v == "":
            return True
        return v.lower() in ('true')

    @classmethod
    def __modify_schema__(cls, field_schema):
        field_schema.update(type="boolean")


# ============================================================================
# RATE LIMITING KEY FUNCTION
# ============================================================================

def rate_limit_key_func(request: Request) -> str:
    """
    Determines the rate-limiting key.
    - For authenticated requests, it uses the peer's node_id.
    - For unauthenticated requests, it falls back to the client's IP address.
    """
    # Prefer the node_id from the header if it exists. This correctly attributes
    # requests to a specific peer identity, even if their IP changes.
    node_id = request.headers.get('x-node-id')
    if node_id:
        return node_id
    
    # For all other requests (e.g., from wallets, miners), use the remote address.
    return get_remote_address(request)

# ============================================================================
# APPLICATION SETUP
# ============================================================================

db: Database = None
self_node_id: str = None
self_is_public: bool = False 

app_servers = [{"url": str(DENARO_SELF_URL)}] if DENARO_SELF_URL else []
app = FastAPI(servers=app_servers, title="Denaro Node API", description="Full node for the Denaro blockchain. GitHub repository: https://github.com/The-Sycorax/denaro", version=NODE_VERSION)

limiter = Limiter(key_func=rate_limit_key_func)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Initialize security components
security = SecureNodeComponents()

# Track startup time
startup_time = time.time()

# Connection pool for HTTP requests
http_client: Optional[httpx.AsyncClient] = None

LAST_PENDING_TRANSACTIONS_CLEAN = [0]
block_processing_lock = asyncio.Lock()

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming HTTP requests using the logger."""
    start_time = time.time()
    
    # Get client IP
    client_ip = request.client.host if request.client else "unknown"
    
    # Build request path with query params
    method = request.method
    path = request.url.path
    query_params = str(request.query_params) if request.query_params else ""
    full_path = f"{path}?{query_params}" if query_params else path
    
    # Truncate very long paths to prevent log spam from malicious requests
    if len(full_path) > LOG_MAX_PATH_LENGTH:
        full_path = full_path[:LOG_MAX_PATH_LENGTH] + "...[TRUNCATED]"
    
    # Extract and format request body if present
    body = None
    body_bytes = None
    if LOG_INCLUDE_REQUEST_CONTENT:
        try:
            # Check if request has a body
            content_length = request.headers.get('content-length')
            
            if content_length and int(content_length) > 0:
                # Read body bytes (this consumes the body, so we'll restore it)
                body_bytes = await request.body()
                
                if body_bytes:
                    # Try to parse as JSON
                    try:
                        body_str = body_bytes.decode('utf-8')
                        parsed_value = json.loads(body_str)
                        
                        # Skip empty collections
                        if isinstance(parsed_value, (dict, list)) and len(parsed_value) == 0:
                            body = None
                        else:
                            # Format as pretty JSON
                            body = json.dumps(parsed_value, indent=2)
                    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                        # Not JSON or not valid UTF-8, use raw string
                        body_str = body_bytes.decode('utf-8', errors='replace')
                        if len(body_str) > 0:
                            body = body_str
                        else:
                            body = None
        except Exception:
            # Silently fail body extraction to not break request processing
            body = None
            body_bytes = None
    
    # Restore request body if we read it (so endpoints can still access it)
    if body_bytes is not None:
        async def receive():
            return {'type': 'http.request', 'body': body_bytes}
        request._receive = receive
    
    # Log incoming request
    request_body_log = f"\n\nIncoming Request:\n{body}\n" if body else ""
    logger.info(f"<-- {client_ip} - \"{method} {full_path} HTTP/1.1\"{request_body_log}")
    
    # Process request
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        status_code = response.status_code
        
        # Extract and format response body if present
        response_body = None
        if LOG_INCLUDE_RESPONSE_CONTENT and hasattr(response, 'body_iterator'):
            try:
                # Read response body
                response_body_bytes = b""
                async for chunk in response.body_iterator:
                    response_body_bytes += chunk
                
                # Always recreate response with the body so client still receives it
                # (body_iterator is consumed after reading)
                response = Response(
                    content=response_body_bytes,
                    status_code=status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type
                )
                
                # Format body for logging
                if response_body_bytes:
                    try:
                        response_body_str = response_body_bytes.decode('utf-8')
                        parsed_value = json.loads(response_body_str)
                        
                        # Skip empty collections
                        if isinstance(parsed_value, (dict, list)) and len(parsed_value) == 0:
                            response_body = None
                        else:
                            # Format as pretty JSON
                            response_body = json.dumps(parsed_value, indent=2)
                    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                        # Not JSON or not valid UTF-8, use raw string
                        response_body_str = response_body_bytes.decode('utf-8', errors='replace')
                        if len(response_body_str) > 0:
                            response_body = response_body_str
                        else:
                            response_body = None
            except Exception:
                # Silently fail response body extraction, return original response
                response_body = None
        
        # Log response
        response_body_log = f"\n\nOutgoing Response:\n{response_body}\n" if response_body else ""
        logger.info(f"--> {client_ip} - \"{method} {full_path} HTTP/1.1\" {status_code}⁢ ({process_time:.3f}s){response_body_log}")
        
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"--> {client_ip} - \"{method} {full_path} HTTP/1.1\" ERROR ({process_time:.3f}s){request_body_log}: {e}")
        raise

async def validate_url_for_connection(url: str) -> bool:
    """Validate URL is safe for outbound connections with DNS rebinding protection"""
    if not url:
        return False
    is_valid, resolved_ip = await security.dns_client.validate_and_resolve(url)
    if not is_valid:
        return False 
    try:
        ip_obj = ipaddress.ip_address(resolved_ip)
        # Block connections to loopback, link-local, or reserved IPs
        if not ip_obj.is_global and not ip_obj.is_private:
             logger.warning(f"Blocked connection to reserved/loopback/link-local IP: {resolved_ip}")
             return False
        # If the node itself is public, it must not initiate connections to private networks
        if self_is_public and ip_obj.is_private:
            logger.warning(f"Public node blocked connection attempt to private IP: {resolved_ip}")
            return False
        return True
    except Exception as e:
        logger.error(f"Unexpected error while validating Address: {url} - {e}")
        return False


async def propagate(path: str, data: dict, ignore_node_id: str = None, db: Database = None):
    """Propagates a message with reputation tracking and rate limiting"""
    async with security.propagation_semaphore:
        all_peers = NodesManager.get_propagate_peers()
        
        # Filter out banned peers
        non_banned_peers = []
        for peer in all_peers:
            if not await security.reputation_manager.is_banned(peer['node_id']):
                non_banned_peers.append(peer)
            
        # Limit number of peers
        max_peers_to_propagate = min(len(non_banned_peers), 20)
        if len(non_banned_peers) > max_peers_to_propagate:
            all_peers = random.sample(non_banned_peers, max_peers_to_propagate)
        else:
            all_peers = non_banned_peers
        
        propagation_path = set(data.get('propagation_path', []))
        our_node_id = get_node_id()
        propagation_path.add(our_node_id)
        peers_to_send = []

        for peer in all_peers:
            peer_id = peer['node_id']

            if peer_id == ignore_node_id:
                continue

            if peer_id in propagation_path:
                logger.debug(f"Skipping propagation to {peer_id}: already in propagation_path")
                continue

            peers_to_send.append(peer)

        for peer in peers_to_send:
            propagation_path.add(peer['node_id'])

        data_to_send = data.copy()
        data_to_send['propagation_path'] = list(propagation_path)

        tasks = []

        for peer in peers_to_send:            
            async def communication_task(peer_info: dict, p: str, d: dict):
                peer_id = peer_info.get('node_id', 'Unknown')
                peer_url = peer_info.get('url')

                try:
                    ni = NodeInterface(peer_info['url'], client=http_client, db=db)
                    
                    if p == 'push_block':
                        response = await ni.push_block(d)
                    elif p == 'push_tx':
                        response = await ni.push_tx(d['tx_hex'])
                    
                    if response and response.get('error') == 'sync_required':
                        if not db:
                            logger.warning(f"Received sync_required from {peer_id} but no DB connection available.")
                            return

                        # Use secure peer sync tracker
                        if await security.peer_sync_tracker.add_peer(peer_id):
                            remote_info = response.get('result', {})
                            next_block_needed = remote_info.get('next_block_expected')

                            if next_block_needed is not None:
                                asyncio.create_task(_push_sync_to_peer(peer_info, next_block_needed, db, d))
                        return

                    # Track successful propagation
                    await security.reputation_manager.record_good_behavior(peer_id)
                    logger.debug(f'propagate response from {peer_id}: {response}')
                
                except httpx.RequestError:
                    # The peer is unreachable (timeout, connection refused, etc.).
                    # This is a network failure, not a protocol violation. Remove them non-punitively.
                    await handle_unreachable_peer(peer_id, peer_url, "propagation")

                except Exception as e:
                    # Network-related failures are not protocol violations — just log them
                    logger.warning(f'propagate EXCEPTION from {peer_id}: {e}')

            tasks.append(communication_task(peer, path, data_to_send))

        await gather(*tasks)


async def _push_sync_to_peer(peer_info: dict, start_block: int, db_conn: Database, trigger_data: dict):
    """Push missing blocks to a lagging peer with proper resource management."""
    peer_id = peer_info.get('node_id', 'Unknown')
    peer_url = peer_info.get('url')
    sync_successful = False
    try:
        target_block_id = trigger_data.get('id')
        if target_block_id is None:
            logger.warning(f"[PUSH-SYNC] Aborted for {peer_id}: No target block ID in trigger data.")
            return

        logger.info(f"[PUSH-SYNC] Starting for {peer_id}. They need blocks from {start_block} up to (but not including) {target_block_id}.")
        
        # Pass the db object to the interface
        node_interface = NodeInterface(peer_info['url'], client=http_client, db=db_conn)
        current_block_to_send = start_block

        while current_block_to_send < target_block_id:
            remaining_blocks = target_block_id - current_block_to_send
            batch_size_limit = min(MAX_REORG_DEPTH, remaining_blocks)
            
            structured_blocks_to_send = await db_conn.get_blocks(current_block_to_send, batch_size_limit)
            if not structured_blocks_to_send:
                logger.warning(f"[PUSH-SYNC] to {peer_id} halted. Local DB has no more blocks in the required range.")
                sync_successful = False
                break

            payload_batch = []
            current_batch_bytes = 0

            for structured_block in structured_blocks_to_send:
                block_record = structured_block['block']
                tx_list = structured_block['transactions']
                block_size_estimate = len(block_record.get('content','')) + sum(len(tx) for tx in tx_list)

                if payload_batch and current_batch_bytes + block_size_estimate > MAX_BATCH_BYTES:
                    break
                
                payload_batch.append({
                    'id': block_record['id'],
                    'block_content': block_record['content'],
                    'txs': tx_list
                })
                current_batch_bytes += block_size_estimate
            
            if not payload_batch:
                logger.warning(f"[PUSH-SYNC] Could not form a batch for {peer_id} without exceeding size limits. Halting.")
                return

            response = await node_interface.push_blocks(payload_batch)
            
            if not response or not response.get('ok'):
                error_msg = response.get('error', '')
                if 'Block sequence out of order' in error_msg or 'sequence desynchronized' in error_msg:
                    logger.info(f"[PUSH-SYNC] to {peer_id} ceded. Peer's state changed, another node is likely already syncing them.")
                else:
                    # Sync rejections can occur from legitimate state changes — not a protocol violation
                    logger.warning(f"[PUSH-SYNC] to {peer_id} failed. Peer responded with an unexpected error: {response}")
                return

            batch_len = len(payload_batch)
            logger.debug(f"[PUSH-SYNC] Peer {peer_id} accepted batch of {batch_len} blocks.")
            current_block_to_send += len(payload_batch)
            await asyncio.sleep(0.1)

        if current_block_to_send >= target_block_id:
            logger.info(f"[PUSH-SYNC] to {peer_id} complete. Sent all blocks up to {target_block_id - 1}.")
            sync_successful = True 
    
    except httpx.RequestError:
        await handle_unreachable_peer(peer_id, peer_url, "push-sync")
        
    except Exception as e:
        logger.error(f"Error during BULK push-sync to {peer_id}: {e}")
        traceback.print_exc()
        
    finally:
        await security.peer_sync_tracker.remove_peer(peer_id)
            
        
        # Only attempt to resubmit if the trigger_data was a full block payload,
        # which we can check by looking for 'block_content'.
        if sync_successful and trigger_data and 'block_content' in trigger_data:
            logger.info(f"[PUSH-SYNC] Sync for {peer_id} complete. Retrying submission of triggering block {trigger_data.get('id')}...")
            try:
                # Recreate the interface to be safe within the finally block
                final_interface = NodeInterface(peer_info['url'], client=http_client, db=db_conn)
                final_response = await final_interface.push_block(trigger_data)
                logger.debug(f"[PUSH-SYNC] Final submission response for block {trigger_data.get('id')} from {peer_id}: {final_response}")
            
            except httpx.RequestError:
                await handle_unreachable_peer(peer_id, peer_url, "push-sync final submission")

            except Exception as e:
                logger.error(f"[PUSH-SYNC] Error during final submission for {peer_id}: {e}")
        

        logger.debug(f"Push-sync task for peer {peer_id} has finished.")


async def check_peer_and_sync(peer_info: dict):
    """
    Checks a given peer's chain status and triggers a sync if their chain is longer.
    This is a core component of the trustless sync mechanism.
    """
    if security.sync_state_manager.is_syncing:
        # Don't start a new sync if one is already in progress to avoid race conditions.
        return

    peer_id = peer_info.get('node_id', 'Unknown')
    peer_url = peer_info.get('url')
    
    try:
        # Ensure the peer is connectable
        if not peer_info.get('url'):
            return
            
        interface = NodeInterface(peer_info['url'], client=http_client, db=db)
        remote_status_resp = await interface.get_status()
        
        if not (remote_status_resp and remote_status_resp.get('ok')):
            # Status unavailability is a network issue, not a protocol violation
            logger.debug(f"Could not get status from peer {peer_id} during check.")
            return

        remote_height = remote_status_resp['result']['height']
        local_height = await db.get_next_block_id() - 1

        if remote_height > local_height:
            logger.info(f"Discovered longer chain on peer {peer_id} (Remote: {remote_height} > Local: {local_height}). Initiating sync.")
            # Trigger the main sync logic, targeting this specific peer
            await _sync_blockchain(node_id=peer_id)
        # If their chain is not longer, there's nothing to do.
    
    except httpx.RequestError:
        await handle_unreachable_peer(peer_id, peer_url, "periodic status check")

    except Exception as e:
        logger.error(f"Error during status check with peer {peer_id}: {e}")


async def get_verified_sender(request: Request):
    """Verifies a request's signature with timestamp validation and reputation tracking"""
    # First check if peer is banned
    is_monitor_request = request.headers.get('x-monitor-request', 'false').lower() == 'true'

    # 2. Perform standard validation and banning checks for ALL requests.
    node_id = request.headers.get('x-node-id')
    if node_id and await security.reputation_manager.is_banned(node_id):
        await security.security_monitor.log_event('banned_peer_attempt', {
            'peer_id': node_id,
            'endpoint': request.url.path
        })
        return None
    
    peer_id = await security.auth_validator.validate_request(request)
    
    if not peer_id:
        return None

    # 3. If it's a monitor request, stop here and return the ID.
    # We have successfully verified them, but we will not treat them as a peer.
    if is_monitor_request:
        if not node_id: # A monitor must still identify itself
            return None
        #print(f"Verified monitor request from node {node_id}. Skipping peer list update.")
        return peer_id

    # 4. If it's a REGULAR peer request, proceed with the normal logic.
    peer_count = len(NodesManager.active_peers) if hasattr(NodesManager, 'peers') else 0
    if peer_count >= MAX_PEERS:
        NodesManager.update_peer_last_seen(peer_id)
        return peer_id
    
    peer_url = request.headers.get('x-peer-url')
    pubkey = request.headers.get('x-public-key')
    peer_version = request.headers.get('x-node-version')
    
    is_unknown = NodesManager.get_peer(peer_id) is None
    
    if is_unknown and pubkey:
        is_peer_public = False
        url_to_store = None

        if peer_url:
            if await validate_url_for_connection(peer_url):
                url_to_store = peer_url
                is_peer_public = not await is_url_local(peer_url)
            else:
                logger.warning(f"Rejected peer URL {peer_url} due to security validation")
                await security.reputation_manager.record_violation(
                    peer_id, 'invalid_url', severity=3
                )
        
        if NodesManager.add_or_update_peer(peer_id, pubkey, url_to_store, is_peer_public, version=peer_version):
            logger.info(f"Discovered new {'public' if is_peer_public else 'private'} peer {peer_id} from their incoming request.")

    NodesManager.update_peer_last_seen(peer_id)
    await security.reputation_manager.record_good_behavior(peer_id)
    
    return peer_id


async def do_handshake_with_peer(peer_url_to_connect: str):
    """
    Performs a cryptographic handshake and state negotiation with a peer. This is the
    client-side of the handshake negotiation.
    """
    peer_id = None

    if not peer_url_to_connect or peer_url_to_connect == DENARO_SELF_URL:
        return

    if not await validate_url_for_connection(peer_url_to_connect):
        logger.warning(f"Skipping handshake with unsafe URL: {peer_url_to_connect}")
        return

    if NodesManager.self_is_public and await is_url_local(peer_url_to_connect):
        logger.debug(f"Public node skipping handshake attempt to private URL: {peer_url_to_connect}")
        return

    logger.info(f"Attempting handshake with {peer_url_to_connect}")
    try:
        interface = NodeInterface(peer_url_to_connect, client=http_client, db=db)
        
        # 1. Get Challenge from peer (which includes their chain state)
        challenge_resp = await interface.handshake_challenge()
        if not (challenge_resp and challenge_resp.get('ok')):
            logger.warning(f"Handshake failed: Did not receive challenge from {peer_url_to_connect}.")
            return

        challenge_data = challenge_resp['result']
        challenge = challenge_data.get('challenge')
        peer_id = challenge_data.get('node_id')
        peer_pubkey = challenge_data.get('pubkey')
        peer_is_public = challenge_data.get('is_public')
        peer_advertised_url = challenge_data.get('url')
        peer_version = challenge_data.get('node_version')
        peer_height = challenge_data.get('height', -1)

        if not all([challenge, peer_id, peer_pubkey, peer_is_public is not None]):
            logger.warning(f"Handshake failed: Incomplete challenge data from {peer_url_to_connect}.")
            return
                   
        # Add or update the peer in our manager AS SOON as we have their info.
        # This makes them "known" before we attempt any sync logic.
        url_to_store = peer_advertised_url if peer_is_public and peer_advertised_url else peer_url_to_connect
        if NodesManager.add_or_update_peer(peer_id, peer_pubkey, url_to_store, peer_is_public, version=peer_version):
            logger.info(f"Discovered and added new {'public' if peer_is_public else 'private'} peer {peer_id}")
        else:
            logger.debug(f"Discovered and updated {'public' if peer_is_public else 'private'} peer {peer_id}")
        

        # 2. Respond to Challenge
        response_resp = await interface.handshake_response(challenge)
        
        # 3. Handle the peer's response. Now we are guaranteed to find the peer in our manager.
        
        # Case A: Peer told us that WE are behind ('sync_required'). We must PULL.
        if response_resp and response_resp.get('error') == 'sync_required':
            logger.info(f"Peer {peer_id} reported that WE are out of sync. Initiating PULL-sync.")
            await _sync_blockchain(node_id=peer_id)
            return
            
        # Case B: Peer is behind and REQUESTED that we PUSH blocks to THEM ('sync_requested').
        if response_resp and response_resp.get('result') == 'sync_requested':
            logger.info(f"Peer {peer_id} is behind and requested a PUSH-sync from us.")
            sync_details = response_resp.get('detail', {})
            start_block = sync_details.get('start_block')
            target_block = sync_details.get('target_block')
            if start_block is not None and target_block is not None:
                peer_info = NodesManager.get_peer(peer_id)
                peer_info['node_id'] = peer_id
                trigger_data = {'id': target_block}
                asyncio.create_task(_push_sync_to_peer(peer_info, start_block, db, trigger_data))
            return

        # Case C: Other failure
        if not (response_resp and response_resp.get('ok')):
            logger.warning(f"Handshake failed: Peer {peer_id} rejected our response: {response_resp}")
            return

        # Case D: Handshake was successful and no sync instruction was given.
        logger.info(f"Handshake SUCCESS with peer {peer_id}.")

        # Fallback PULL trigger: If peer didn't respond with instructions but we see they are ahead.
        local_height = await db.get_next_block_id() - 1
        if peer_height > local_height:
             logger.info(f"Peer {peer_id} has longer chain ({peer_height} > {local_height}). Initiating PULL-sync.")
             await _sync_blockchain(node_id=peer_id)
        
        # 4. Perform peer exchange
        logger.debug(f"Performing peer exchange with {peer_id}")
        peers_resp = await interface.get_peers()
        if peers_resp and peers_resp.get('ok'):
            for discovered_peer in peers_resp['result']['peers']:
                if discovered_peer.get('url') and discovered_peer['node_id'] not in NodesManager.active_peers and discovered_peer['node_id'] != self_node_id:
                    if len(NodesManager.active_peers) < MAX_PEERS:
                        logger.info(f"Found node {discovered_peer['node_id']} via exchange. Attempting handshake.")
                        asyncio.create_task(do_handshake_with_peer(discovered_peer['url']))
    
    except httpx.RequestError:
        peer_id = NodesManager.find_peer_by_url(peer_url_to_connect)
        if peer_id:
            await handle_unreachable_peer(peer_id, peer_url_to_connect, "handshake")
        else:
            # If we don't know them, we can't remove them, just log it.
            logger.warning(f"Failed to connect to unknown or new peer at {peer_url_to_connect} during handshake.")

    except Exception as e:
        logger.error(f"Error during handshake with {peer_url_to_connect}: {e}")
        traceback.print_exc()
        await security.security_monitor.log_event('handshake_failure', {
            'url': peer_url_to_connect,
            'error': str(e)
        })


async def periodic_peer_discovery():
    """
    Periodically discovers new peers via gossip and verifies them via handshake.
    This version is now resilient to unreachable peers.
    """
    await asyncio.sleep(20)
    await do_handshake_with_peer(DENARO_BOOTSTRAP_NODE)

    while True:
        await asyncio.sleep(60)
        logger.debug("Running periodic peer discovery...")
        
        if not NodesManager.active_peers:
            logger.info("Peer list is empty. Retrying handshake with bootstrap node.")
            await do_handshake_with_peer(DENARO_BOOTSTRAP_NODE)
            continue

        connectable_peers_tuples = [
            (node_id, peer_data) for node_id, peer_data in NodesManager.active_peers.items() if peer_data.get('url')
        ]

        if not connectable_peers_tuples:
            logger.debug("No connectable peers to ask for discovery. Waiting for new inbound connections.")
            continue

        
        # Define peer_id and peer_url here so they are in scope for the entire loop iteration.
        peer_id, peer_data_to_ask = random.choice(connectable_peers_tuples)
        peer_url = peer_data_to_ask['url']
        
        
        logger.debug(f"Asking peer {peer_id} for their peer list")
        try:
            # Use the correctly scoped variables
            interface = NodeInterface(peer_url, client=http_client, db=db)
            peers_resp = await interface.get_peers()
            
            if not (peers_resp and peers_resp.get('ok')):
                continue
            
            discovered_peers = peers_resp['result']['peers']
            logger.debug(f"Discovered {len(discovered_peers)} peers from {peer_id}.")
            for discovered_peer in discovered_peers:
                if discovered_peer['node_id'] not in NodesManager.active_peers and discovered_peer['node_id'] != self_node_id:
                    if len(NodesManager.active_peers) < MAX_PEERS and discovered_peer.get('url'):
                        logger.info(f"Found new peer {discovered_peer['node_id']} via exchange. Attempting handshake.")
                        asyncio.create_task(do_handshake_with_peer(discovered_peer['url']))
        
        except httpx.RequestError:
            await handle_unreachable_peer(peer_id, peer_url, "peer discovery")

        except Exception as e:
            # Use the correctly scoped variable in the log message
            logger.error(f"Error during peer discovery with {peer_url}: {e}")


async def is_url_local(url: str) -> bool:
    try:
        hostname = urlparse(url).hostname
        if not hostname: return False
        addr_info = await asyncio.get_event_loop().getaddrinfo(hostname, None, family=socket.AF_INET)
        ip_obj = ipaddress.ip_address(addr_info[0][4][0])
        return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
    except (socket.gaierror, ValueError, IndexError):
        return False


async def check_own_reachability():
    """A one-time startup task to determine if the node is publicly reachable"""
    global self_is_public
    await asyncio.sleep(10)

    if not DENARO_SELF_URL:
        logger.info("DENARO_SELF_URL not set. Operating as a private node.")
        NodesManager.set_public_status(False)
        return

    if await is_url_local(DENARO_SELF_URL):
        logger.info(f"DENARO_SELF_URL is a local address ({DENARO_SELF_URL}). Operating as a private node.")
        self_is_public = False
        NodesManager.set_public_status(False)
        return

    logger.info(f"Potential public URL is {DENARO_SELF_URL}. Asking bootstrap node to verify reachability.")
    bootstrap_interface = NodeInterface(DENARO_BOOTSTRAP_NODE, client=http_client, db=db)
    
    try:
        is_reachable = await bootstrap_interface.check_peer_reachability(DENARO_SELF_URL)
        if is_reachable:
            self_is_public = True
            NodesManager.set_public_status(True)
            logger.info(f"Node confirmed to be publicly reachable at {DENARO_SELF_URL}")
        else:
            self_is_public = False
            NodesManager.set_public_status(False)
            logger.info(f"DENARO_SELF_URL is set to {DENARO_SELF_URL}, but it was not reachable by the bootstrap node. Operating as a private node.")
    

    except httpx.RequestError:
        # The bootstrap node is unreachable. We can't verify, so we must assume we are private.
        self_is_public = False
        NodesManager.set_public_status(False)
        logger.info(f"Bootstrap node at {DENARO_BOOTSTRAP_NODE} is unreachable. Operating as a private node.")
    
    except Exception as e:
        self_is_public = False
        NodesManager.set_public_status(False)
        logger.info("Failed to verify reachability with bootstrap node. Operating as a private node.")
        logger.error(e)


async def periodic_update_fetcher():
    """
    A background task that runs for ALL nodes to discover new blocks and transactions.
    It periodically polls random peers to ensure the node is on the heaviest chain
    and to learn about new unconfirmed transactions.
    """
    await asyncio.sleep(30) 

    logger.info("Starting periodic update fetcher for this node...")
    while True:
        await asyncio.sleep(60) 
        
        # 1. CHECK FOR LONGER CHAINS (BLOCK SYNC)
        if not security.sync_state_manager.is_syncing:
            all_peers = NodesManager.get_all_peers()
            connectable_peers = [p for p in all_peers if p.get('url')]

            if connectable_peers:
                # Probe up to 2 random peers to find a potentially longer chain
                peers_to_probe = random.sample(connectable_peers, k=min(len(connectable_peers), 2))
                logger.debug(f"Probing {len(peers_to_probe)} peer(s) for a longer chain")
                for peer_info in peers_to_probe:
                    await check_peer_and_sync(peer_info)
                    await asyncio.sleep(1) # Small delay between probes
        
        # 2. CHECK FOR NEW TRANSACTIONS (MEMPOOL SYNC)
        all_peers = NodesManager.get_all_peers()
        connectable_peers = [p for p in all_peers if p.get('url')]

        if not connectable_peers:
            continue

        # Ask one random peer for their mempool
        peer_to_ask = random.choice(connectable_peers)
        interface = NodeInterface(peer_to_ask['url'], client=http_client, db=db)
        peer_id = peer_to_ask['node_id']
        peer_url = peer_to_ask['url']
        
        logger.debug(f"Polling peer {peer_id} for new transactions")
        try:
            mempool_hashes_resp = await interface.get_mempool_hashes()
            
            if mempool_hashes_resp is None or not mempool_hashes_resp.get('ok'):
                logger.warning(f"Could not get mempool hashes from {peer_id}. Skipping transaction sync.")
                continue 

            remote_hashes = set(mempool_hashes_resp['result'])
            local_hashes = set(await db.get_all_pending_transaction_hashes())
            needed_hashes = list(remote_hashes - local_hashes)

            if needed_hashes:
                logger.debug(f"Discovered {len(needed_hashes)} new transaction(s) from {peer_id}. Fetching...")
                
                for i in range(0, len(needed_hashes), MAX_TX_FETCH_LIMIT):
                    batch = needed_hashes[i:i+MAX_TX_FETCH_LIMIT]
                    fetched_txs_resp = await interface.get_transactions_by_hash(batch)
                    
                    if fetched_txs_resp and fetched_txs_resp.get('ok'):
                        transactions_to_propagate = []
                        for tx_hex in fetched_txs_resp['result']:
                            try:
                                is_valid, error_msg = security.input_validator.validate_transaction_data(tx_hex)
                                if not is_valid:
                                    logger.warning(f"Skipping invalid transaction from peer: {error_msg}")
                                    continue
                                    
                                tx = await Transaction.from_hex(tx_hex)
                                if await security.transaction_pool.add_transaction(tx.hash(), tx, db):
                                    logger.debug(f"  -> Accepted new pending transaction {tx.hash()[:10]}")
                                    transactions_to_propagate.append(tx_hex)
                            except Exception as e:
                                logger.error(f"Error processing fetched transaction: {e}")
                        
                        if transactions_to_propagate:
                            logger.debug(f"Propagating {len(transactions_to_propagate)} newly learned transactions...")
                            for tx_hex in transactions_to_propagate:
                                asyncio.create_task(
                                    propagate('push_tx', {'tx_hex': tx_hex}, ignore_node_id=peer_to_ask['node_id'])
                                )
                    else:
                        logger.warning(f"Failed to fetch full transaction data for batch.")
        
        except httpx.RequestError:
            await handle_unreachable_peer(peer_id, peer_url, "periodic mempool fetch")

        except Exception as e:
            logger.error(f"An unexpected error occurred during periodic fetch from {peer_id}: {e}")
            traceback.print_exc()


async def process_and_create_block(block_info: dict) -> bool:
    """Processes a single block dictionary with validation"""
    block = block_info['block']
    txs_hex = block_info['transactions']
    block_content = block.get('content')
    
    # Validate block content size
    if len(block_content) > MAX_BLOCK_CONTENT_SIZE:
        logger.warning(f"Sync failed: Block content too large for block {block.get('id')}.")
        return False
    
    try:
        transactions = []
        for tx_hex in txs_hex:
            is_valid, error_msg = security.input_validator.validate_transaction_data(tx_hex)
            if not is_valid:
                logger.warning(f"Sync failed: Invalid transaction in block {block.get('id')}: {error_msg}")
                return False
            transactions.append(await Transaction.from_hex(tx_hex))

    except Exception as e:
        logger.error(f"Sync failed: Could not deserialize transactions for block {block.get('id')}: {e}")
        return False

    if not await create_block(block_content, transactions):
        logger.warning(f"Sync failed: Invalid block received from peer at height {block.get('id')}.")
        return False
        
    return True


async def handle_reorganization(node_interface: NodeInterface, local_height: int):
    """Handles blockchain reorganization with proper validation"""
    logger.warning(f"[REORG] Fork detected! Starting reorganization process from local height {local_height}.")

    last_common_block_id = -1
    check_height = local_height

    # Find the last common block
    while check_height >= 0:
        local_block = await db.get_block_by_id(check_height)
        if not local_block:
            logger.error(f"[REORG] Error: Could not retrieve local block at height {check_height}. Halting search.")
            break

        try:
            remote_block_info = await node_interface.get_block(str(check_height))
            if remote_block_info and remote_block_info.get('ok'):
                remote_hash = remote_block_info['result']['block']['hash']
                if remote_hash == local_block['hash']:
                    last_common_block_id = check_height
                    logger.info(f"[REORG] Found common ancestor at block height: {last_common_block_id}")
                    break
            else:
                logger.warning(f"[REORG] Could not get remote block at height {check_height}. Aborting search.")
                return None
        
        except httpx.RequestError:
            logger.warning(f"[REORG] Peer became unreachable during common ancestor search. Aborting.")
            # Let the caller handle the peer removal.
            return None

        except Exception as e:
            logger.error(f"[REORG] Network error while finding common ancestor at height {check_height}: {e}")
            return None

        if (local_height - check_height) > 200:
            logger.warning("[REORG] Reorganization depth exceeds 200 blocks. Aborting for safety.")
            return None
        
        check_height -= 1

    if last_common_block_id == -1:
        logger.warning("[REORG] Could not find a common ancestor. Local chain appears invalid. Will perform a full rollback.")

    logger.info(f"[REORG] Collecting transactions from orphaned blocks between {last_common_block_id + 1} and {local_height}.")
    orphaned_txs = []
    for height in range(last_common_block_id + 1, local_height + 1):
        block = await db.get_block_by_id(height)
        if block:
            block_txs = await db.get_block_transactions(block['hash'], hex_only=False)
            orphaned_txs.extend([tx for tx in block_txs if not isinstance(tx, CoinbaseTransaction)])

    logger.info(f"[REORG] Rolling back local chain to block {last_common_block_id}.")
    await db.remove_blocks(last_common_block_id + 1)

    logger.info(f"[REORG] Re-adding {len(orphaned_txs)} orphaned transactions to the pending pool.")
    for tx in orphaned_txs:
        try:
            await security.transaction_pool.add_transaction(tx.hash(), tx, db)
        except Exception as e:
            logger.error(f"[REORG] Could not re-add orphaned transaction {tx.hash()}: {e}")

    return last_common_block_id


async def _sync_blockchain(node_id: str = None): 
    """Synchronizes the local blockchain with proper state management"""
    try:
        async with security.sync_state_manager.acquire_sync():
            logger.info('[SYNC] Starting blockchain synchronization process...')

            peer_to_sync_from = None
            if node_id:
                peer_to_sync_from = NodesManager.get_peer(node_id)
                if peer_to_sync_from:
                    peer_to_sync_from['node_id'] = node_id
            else:
                active_peers = NodesManager.get_propagate_peers(limit=1)
                if active_peers:
                    peer_to_sync_from = active_peers[0]

            if not peer_to_sync_from:
                logger.warning("[SYNC] Aborting: No known (or specified) peer to sync from.")
                return

            peer_url = peer_to_sync_from['url']
            peer_id = peer_to_sync_from['node_id']
            logger.info(f"[SYNC] Attempting to sync with peer {peer_id} at {peer_url}")
            
            node_interface = NodeInterface(peer_url, client=http_client, db=db)

            last_local_block = await db.get_last_block()
            local_height = last_local_block['id'] if last_local_block else -1
            
            remote_status_resp = await node_interface.get_status()

            if not (remote_status_resp and remote_status_resp.get('ok')):
                logger.warning(f"[SYNC] Failed to get chain status from {peer_url}. Aborting.")
                return
                
            remote_status = remote_status_resp['result']
            remote_height = remote_status['height']
            
            logger.info(f"[SYNC] Local height: {local_height}, Remote height: {remote_height}")

            if remote_height <= local_height:
                logger.debug("[SYNC] Local chain is at or ahead of remote. No sync needed.")
                return

            logger.info("[SYNC] Remote chain is longer.")
            
            fork_detected = False
            if local_height > -1:
                local_last_hash = last_local_block['hash']
                remote_block_resp = await node_interface.get_block(str(local_height))
                
                if not (remote_block_resp and remote_block_resp.get('ok')):
                    logger.warning("[SYNC] Could not fetch remote block for integrity check. Aborting.")
                    return
                
                remote_block_at_our_height = remote_block_resp['result']
                if remote_block_at_our_height['block']['hash'] != local_last_hash:
                    logger.warning(f"[SYNC] Fork detected. Our tip is on a shorter fork.")
                    fork_detected = True
            else:
                logger.info("[SYNC] Local chain is empty. Beginning initial block download.")

            if fork_detected:
                reorg_result = await handle_reorganization(node_interface, local_height)
                if reorg_result is None:
                    logger.error("[SYNC] Reorganization failed. Aborting sync cycle.")
                    return
            
            logger.info("[SYNC] Starting block fetching process.")
            while True:
                start_block_id = await db.get_next_block_id()

                if start_block_id > remote_height:
                    logger.info("[SYNC] Local height now meets or exceeds remote height. Sync appears complete.")
                    break

                logger.debug(f"[SYNC] Fetching {MAX_REORG_DEPTH} blocks starting from block {start_block_id}...")
                
                blocks_resp = await node_interface.get_blocks(start_block_id, MAX_REORG_DEPTH)
                
                if not (blocks_resp and blocks_resp.get('ok')):
                    logger.warning("[SYNC] Failed to fetch a batch of blocks from peer. Aborting sync cycle.")
                    break
                
                blocks_batch = blocks_resp['result']
                if not blocks_batch:
                    logger.info('[SYNC] No more blocks returned by peer. Sync presumed complete.')
                    break
                
                for block_data in blocks_batch:
                    if not await process_and_create_block(block_data):
                        logger.error("[SYNC] FATAL ERROR: Failed to create blocks during sync. Aborting.")
                        await security.reputation_manager.record_violation(
                            peer_to_sync_from['node_id'], 'invalid_sync_block', severity=8
                        )
                        return
                    await asyncio.sleep(0)

                NodesManager.update_peer_last_seen(peer_to_sync_from['node_id'])
    

    except httpx.RequestError:
        if peer_to_sync_from:
            await handle_unreachable_peer(peer_to_sync_from.get('node_id', 'unknown'),  peer_to_sync_from.get('url'), "blockchain sync")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SYNC] An unexpected error occurred during the sync process: {e}")
        import traceback
        traceback.print_exc()
    finally:
        logger.info('[SYNC] Synchronization process finished.')


async def handle_unreachable_peer(peer_id: str, peer_url: str, context: str):
    """
    Centralized handler for when a peer is unreachable.
    This action is NOT punitive. It simply removes the peer from the active
    list for this session to prevent wasting resources. The peer can be re-discovered later.
    """
    logger.warning(f"Peer {peer_id} at {peer_url} is unreachable ({context}). Removing from active peer list.")
    NodesManager.remove_peer(peer_id)



# ============================================================================
# APPLICATION STARTUP/SHUTDOWN
# ============================================================================

@app.on_event("startup")
async def startup():
    global db, self_node_id, http_client  # Add http_client here
    
    logger.info("Starting Denaro Node Server...")

    os.makedirs(DENARO_DATA_DIR, exist_ok=True)


    # Initialize the shared HTTP client for the application's lifespan
    http_client = httpx.AsyncClient(timeout=CONNECTION_TIMEOUT)
    logger.info("Shared HTTP client initialized.")
    
    # Initialize security components
    await security.startup()
    
    #NodesManager.purge_active_peers()
    initialize_identity()
    self_node_id = get_node_id()
    NodesManager.init(self_node_id)

    db = await Database.create(
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=DENARO_DATABASE_NAME,
        host=DENARO_DATABASE_HOST
    )
    
    logger.info("Clearing pending transaction pool.")
    await db.remove_all_pending_transactions()
    logger.info("Pending transaction pool cleared.")
    
    logger.info("Starting background tasks.")
    asyncio.create_task(check_own_reachability())
    asyncio.create_task(periodic_peer_discovery())
    asyncio.create_task(periodic_update_fetcher())

    logger.info(f"Denaro node server started on http://{DENARO_NODE_HOST}:{DENARO_NODE_PORT}")
    logger.info("Application startup complete.")
    

@app.on_event("shutdown")
async def shutdown():
    """Clean shutdown"""
    # Close the shared HTTP client
    if http_client:
        await http_client.aclose()
        logger.info("Shared HTTP client closed.")
        
    await security.shutdown()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.critical(f"Unhandled exception: {exc}")
    logger.debug(traceback.format_exc())
    
    await security.security_monitor.log_event('unhandled_exception', {
        'endpoint': request.url.path,
        'error': str(exc)
    })
    
    return JSONResponse(status_code=500, content={"ok": False, "error": "Internal Server Error"})


# ============================================================================
# MIDDLEWARE
# ============================================================================

async def propagate_old_transactions(propagate_txs):
    await db.update_pending_transactions_propagation_time([sha256(tx_hex) for tx_hex in propagate_txs])
    for tx_hex in propagate_txs:
        await propagate('push_tx', {'tx_hex': tx_hex})


@app.middleware("http")
async def middleware(request: Request, call_next):
    """Simple middleware to handle URL normalization and attach background tasks"""
    path = request.scope['path']
    normalized_path = re.sub('/+', '/', path)
    if normalized_path != path:
        new_url = str(request.url.replace(path=normalized_path))
        return RedirectResponse(url=new_url)

    try:
        propagate_txs = await db.get_need_propagate_transactions()
        response = await call_next(request)
        
        if propagate_txs:
            existing_background = response.background or BackgroundTasks()
            existing_background.add_task(propagate_old_transactions, propagate_txs)
            response.background = existing_background
            
        return response
    except Exception:
        raise


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """
    Root endpoint that returns the node version, github repository, and API documentation URL.<br>
    
    - Returns:
      - dict: Contains 'node_version', 'github_repository', and 'api_docs'.
    """
    api_docs_url = f"{DENARO_SELF_URL}/docs" if DENARO_SELF_URL else None
    result = {
        "node_version": NODE_VERSION,
        "github_repository": "https://github.com/The-Sycorax/denaro",
        "api_docs": api_docs_url
    }
    return result


@app.post("/push_tx")
@limiter.limit("100/minute")
async def push_tx(
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    verified_sender: str = Depends(get_verified_sender)
):
    """
    Authenticated endpoint to receive a transaction from a verified peer.<br>
    
    Validates the transaction structure and content. If valid and new, adds it to the<br>
    local mempool and propagates it to other peers.<br>

    - Returns:
      - dict: Success status and result message or error.
    
    - Raises:
      - HTTPException: If sender is unverified, tx is missing, or validation fails.
    """
    if not verified_sender:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signed request required.")

    tx_hex = body.get('tx_hex')
    if not tx_hex:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'tx_hex' not found in body.")

    is_valid, error_msg = security.input_validator.validate_transaction_data(tx_hex)
    if not is_valid:
        await security.reputation_manager.record_violation(
            verified_sender, 'invalid_transaction', severity=2, details=error_msg
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error_msg)

    tx = await Transaction.from_hex(tx_hex)
    
    
    # Verify the transaction before accepting it into the mempool.
    try:
        if not await tx.verify():
            await security.reputation_manager.record_violation(
                verified_sender, 'invalid_transaction_content', severity=5, details="Transaction failed full verification"
            )
            return {'ok': False, 'error': 'Transaction verification failed'}
    except Exception as e:
        await security.reputation_manager.record_violation(
            verified_sender, 'invalid_transaction_content', severity=5, details=f"Verification error: {e}"
        )
        return {'ok': False, 'error': f'Transaction verification failed: {e}'}
    

    if await security.transaction_cache.contains(tx.hash()):
        return {'ok': False, 'error': 'Transaction just added'}
    
    pending_count = await db.get_pending_transaction_count()
    if pending_count >= MAX_MEMPOOL_SIZE:
        await security.security_monitor.log_event('mempool_full', {
            'peer_id': verified_sender,
            'pending_count': pending_count
        })
        return {'ok': False, 'error': 'Mempool is full'}
    
    try:
        if await security.transaction_pool.add_transaction(tx.hash(), tx, db):
            logger.info(f"Accepted transaction {tx.hash()} from external client. Propagating to network.")
            background_tasks.add_task(
                propagate, 'push_tx', {'tx_hex': tx_hex}, 
                ignore_node_id=verified_sender
            )
            await security.transaction_cache.put(tx.hash(), True)
            return {'ok': True, 'result': 'Transaction has been accepted'}
        else:
            return {'ok': False, 'error': 'Transaction has not been added'}
    except UniqueViolationError:
        return {'ok': False, 'error': 'Transaction already present'}
    except Exception as e:
        await security.security_monitor.log_event('transaction_error', {
            'peer_id': verified_sender,
            'error': str(e)
        })
        return {'ok': False, 'error': 'Transaction rejected'}


@app.post("/submit_block")
@limiter.limit("12/minute")
async def submit_block(
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
):
    """
    Endpoint that is used by miners to submit a newly mined block.<br>
    
    This endpoint accepts blocks without a peer signature (as miners might not be<br>
    full nodes). Performs heavy validation on block content, difficulty, and<br>
    transactions. If valid, the block is appended to the chain and propagated.<br>

    - Returns:
      - dict: Success status and result message or error.

    - Raises:
      - HTTPException: If block data is missing, too large, or invalid.
    """
    
    block_content = body.get('block_content')
    if not block_content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing block_content.")

    # Validate block content size
    if len(block_content) > MAX_BLOCK_CONTENT_SIZE:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Block content too large.")

    block_identifier = sha256(block_content.encode())
    
    # Use time-based cache
    if await security.block_cache.contains(block_identifier):
        return {'ok': False, 'error': 'Block recently seen'}
    
    # Check sync state
    if security.sync_state_manager.is_syncing:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={'ok': False, 'error': 'Node is busy synchronizing, please try again later.'}
        )

    # Block processing lock prevents race conditions
    async with block_processing_lock:
        txs_data = body.get('txs', [])
        block_no = body.get('id') or body.get('block_no')

        if not all([txs_data is not None, block_no is not None]):
             raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing block data.")

        # Validate block height
        if not await security.input_validator.validate_block_height(block_no, db, max_ahead=1):
            return {'ok': False, 'error': 'Invalid block height'}

        next_block_id = await db.get_next_block_id()
        
        # Only accept blocks that build directly on our current chain tip
        if next_block_id != block_no:
            return {'ok': False, 'error': f'Invalid block height. Expected {next_block_id}, got {block_no}. This may be a stale block.'}
        
        await security.block_cache.put(block_identifier, True)

        final_transactions = []
        tx_hashes_to_find = []
        if isinstance(txs_data, str):
            txs_data = txs_data.split(',') if txs_data else []
        for tx_hex in txs_data:
            if isinstance(tx_hex, str) and len(tx_hex) == 64:
                tx_hashes_to_find.append(tx_hex)
            else:
                is_valid, error_msg = security.input_validator.validate_transaction_data(tx_hex)
                if not is_valid:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Transaction data within block is invalid: {error_msg}")
                final_transactions.append(await Transaction.from_hex(tx_hex))

        if tx_hashes_to_find:
            db_results = await db.get_pending_transactions_by_hash(tx_hashes_to_find)
            if len(db_results) < len(tx_hashes_to_find):
                return {'ok': False, 'error': 'One or more transaction hashes not found in pending pool.'}

            tx_map = {tx.hash(): tx for tx in db_results}
            ordered_txs = [tx_map.get(tx_hash) for tx_hash in tx_hashes_to_find]
            final_transactions.extend(ordered_txs)
        
        if not await create_block(block_content, final_transactions):
            return {'ok': False, 'error': 'Block failed validation.'}

        miner_ip = request.client.host
        logger.info(f"Accepted block {block_no} from miner at {miner_ip}. Propagating to network...")
        
        # Propagate to all peers
        body_to_propagate = body.copy()
        body_to_propagate['propagation_path'] = [get_node_id()]
        background_tasks.add_task(propagate, 'push_block', body_to_propagate, ignore_node_id=None, db=db) 
        
        return {'ok': True, 'result': f'Block {block_no} accepted.'}


@app.post("/submit_tx")
@limiter.limit("30/minute") 
async def submit_tx(
    request: Request, 
    background_tasks: BackgroundTasks,
    body: dict = Body(...)
):
    """
    Endpoint for submitting a transaction (e.g., from a wallet).<br>

    - Returns:
      - dict: Success status and result message or error.

    - Raises:
      - HTTPException: If validation fails.
    """
    tx_hex = body.get('tx_hex')
    if not tx_hex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'tx_hex' not found in body.")

    is_valid, error_msg = security.input_validator.validate_transaction_data(tx_hex)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

    try:
        tx = await Transaction.from_hex(tx_hex)
        
        # Verify the transaction before accepting it.
        if not await tx.verify():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transaction verification failed.")
        
    except Exception as e:
        # Catch verification errors or deserialization errors
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid transaction: {e}")

    if await security.transaction_cache.contains(tx.hash()):
        return {'ok': False, 'error': 'Transaction recently seen'}

    pending_count = await db.get_pending_transaction_count()
    if pending_count >= MAX_MEMPOOL_SIZE:
        return {'ok': False, 'error': 'Mempool is full'}

    try:
        if await security.transaction_pool.add_transaction(tx.hash(), tx, db):
            logger.info(f"Accepted transaction {tx.hash()} from external client. Propagating to network...")
            background_tasks.add_task(propagate, 'push_tx', {'tx_hex': tx_hex}, ignore_node_id=None)
            await security.transaction_cache.put(tx.hash(), True)
            return {'ok': True, 'result': 'Transaction has been accepted'}
        else:
            return {'ok': False, 'error': 'Transaction failed validation'}
    except UniqueViolationError:
        return {'ok': False, 'error': 'Transaction already present in pending pool'}
    except Exception as e:
        return {'ok': False, 'error': 'Transaction rejected'}





@app.post("/push_block")
@limiter.limit("20/minute")
async def push_block(
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    verified_sender: str = Depends(get_verified_sender)
):
    """
    Authenticated endpoint to receive a block from a verified peer.<br>
    
    Validates block content, difficulty, and transactions. If valid, the block is<br>
    appended to the chain and propagated to other peers.<br>

    - Returns:
      - dict: Success status and result message or error.

    - Raises:
      - HTTPException: If sender is unverified, block is missing, or validation fails.
    """
        
    if not verified_sender:
        # Quick workaround to add support for legacy miners that use the old endpoint name.
        # Delegates request to submit_block which handles unauthenticated miner requests.
        # A more secure solution may be needed for this in the future.
        node_id = request.headers.get('x-node-id')
        signature = request.headers.get('x-signature')
        if not node_id or not signature:
            return await submit_block(request, background_tasks, body)
        
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signed request required.")


    block_content = body.get('block_content')
    if not block_content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing block_content.")

    # Validate block content size
    if len(block_content) > MAX_BLOCK_CONTENT_SIZE:
        await security.reputation_manager.record_violation(
            verified_sender, 'oversized_block', severity=3
        )
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Block content too large.")

    block_identifier = sha256(block_content.encode())
    
    # Use time-based cache
    if await security.block_cache.contains(block_identifier):
        return {'ok': False, 'error': 'Block recently seen'}
    
    # Check sync state
    if security.sync_state_manager.is_syncing:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={'ok': False, 'error': 'Node is busy synchronizing, please try again later.'}
        )

    async with block_processing_lock:
        block_no = body.get('id') or body.get('block_no')
        if block_no is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing block ID.")
        
        # Validate block height
        if not await security.input_validator.validate_block_height(block_no, db):
            await security.reputation_manager.record_violation(
                verified_sender, 'invalid_block_height', severity=4
            )
            return {'ok': False, 'error': 'Invalid block height'}
        
        next_block_id = await db.get_next_block_id()
        if next_block_id > block_no:
            return {'ok': False, 'error': 'Too old block'}
        
        if next_block_id < block_no:
            # Check if peer is already syncing
            if await security.peer_sync_tracker.is_syncing(verified_sender):
                return {'ok': False, 'error': 'Already syncing to this peer'}
                
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    'ok': False, 
                    'error': 'sync_required',
                    'result': {
                        'next_block_expected': next_block_id
                    }
                }
            )
        
        await security.block_cache.put(block_identifier, True)
        
        # Process transactions with validation
        txs_data = body.get('txs', [])
        final_transactions = []
        tx_hashes_to_find = []
        
        if isinstance(txs_data, str):
            txs_data = txs_data.split(',') if txs_data else []
            
        for tx_hex in txs_data:
            if isinstance(tx_hex, str) and len(tx_hex) == 64 and VALID_HEX_PATTERN.match(tx_hex):
                tx_hashes_to_find.append(tx_hex)
            else:
                is_valid, error_msg = security.input_validator.validate_transaction_data(tx_hex)
                if not is_valid:
                    await security.reputation_manager.record_violation(
                        verified_sender, 'invalid_block_transaction', severity=5
                    )
                    return {'ok': False, 'error': f'Invalid transaction in block: {error_msg}'}
                    
                final_transactions.append(await Transaction.from_hex(tx_hex))
                
        if tx_hashes_to_find:
            db_results = await db.get_pending_transactions_by_hash(tx_hashes_to_find)
            if len(db_results) < len(tx_hashes_to_find):
                return {'ok': False, 'error': 'Transaction hash not found.'}
                
            tx_map = {tx.hash(): tx for tx in db_results}
            ordered_txs = [tx_map.get(tx_hash) for tx_hash in tx_hashes_to_find]
            final_transactions.extend(ordered_txs)
        
        if not await create_block(block_content, final_transactions):
            await security.reputation_manager.record_violation(
                verified_sender, 'invalid_block', severity=7
            )
            return {'ok': False, 'error': 'Block failed validation.'}

        # Record successful block
        await security.reputation_manager.record_good_behavior(verified_sender, points=5)
        
        logger.info(f"Accepted block {block_no} from {verified_sender}. Propagating to network...")

        propagation_path = set(body.get('propagation_path', []))
        propagation_path.add(verified_sender)
        body_to_propagate = body.copy()
        body_to_propagate['propagation_path'] = list(propagation_path)
        background_tasks.add_task(propagate, 'push_block', body_to_propagate, ignore_node_id=verified_sender, db=db)

        return {'ok': True, 'result': f'Block {block_no} accepted.'}
        

@app.post("/push_blocks")
@limiter.limit("5/minute")
async def push_blocks(
    request: Request,
    body: list = Body(...),
    verified_sender: str = Depends(get_verified_sender)
):
    """
    Authenticated endpoint to receive multiple blocks in a single batch from a verified peer (used for sync operations).<br>
    
    Validates and processes a sequence of blocks. Unlike /push_block, this endpoint<br>
    does NOT propagate blocks (they are already being pushed from a syncing peer).<br>
    Blocks must be submitted in continuous sequence.<br>

    - Returns:
      - dict: Success status with count of processed blocks or error message.
    """
        
    if not verified_sender:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signed request required.")

    if not isinstance(body, list) or not body:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Request body must be a non-empty list.")

    if len(body) > MAX_BLOCKS_PER_SUBMISSION:
        await security.reputation_manager.record_violation(
            verified_sender, 'too_many_blocks', severity=3
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Request limit exceeded. You can only submit up to {MAX_BLOCKS_PER_SUBMISSION} blocks at a time."
        )
    
    try:
        async with security.sync_state_manager.acquire_sync():
            async with block_processing_lock:
                blocks_to_process = sorted(body, key=lambda x: x['id'])
                
                # Verify continuity
                for i in range(1, len(blocks_to_process)):
                    if blocks_to_process[i]['id'] != blocks_to_process[i-1]['id'] + 1:
                        await security.reputation_manager.record_violation(
                            verified_sender, 'non_continuous_blocks', severity=4
                        )
                        return {'ok': False, 'error': 'Block sequence must be continuous'}
                
                next_block_id = await db.get_next_block_id()
                if blocks_to_process[0]['id'] != next_block_id:
                    return {'ok': False, 'error': f'Block sequence out of order. Expected {next_block_id}, got {blocks_to_process[0]["id"]}.'}

                for block_payload in blocks_to_process:
                    block_no = block_payload.get('id')
                    
                    current_expected_id = await db.get_next_block_id()
                    if current_expected_id != block_no:
                        return {'ok': False, 'error': f'Block sequence desynchronized during batch. Expected {current_expected_id}, got {block_no}.'}
                    
                    block_content = block_payload.get('block_content')
                    txs_data = block_payload.get('txs', [])

                    if not block_content:
                        return {'ok': False, 'error': f'Invalid block data for block {block_no}: missing content.'}

                    # Validate block content size
                    if len(block_content) > MAX_BLOCK_CONTENT_SIZE:
                        await security.reputation_manager.record_violation(
                            verified_sender, 'oversized_bulk_block', severity=3
                        )
                        return {'ok': False, 'error': f'Block {block_no} content too large.'}

                    block_identifier = sha256(block_content.encode())
                    if await security.block_cache.contains(block_identifier):
                        continue
                    
                    final_transactions = []
                    for tx_hex in txs_data:
                        is_valid, error_msg = security.input_validator.validate_transaction_data(tx_hex)
                        if not is_valid:
                            await security.reputation_manager.record_violation(
                                verified_sender, 'invalid_bulk_transaction', severity=5
                            )
                            return {'ok': False, 'error': f'Block {block_no} contains an invalid transaction: {error_msg}'}
                        final_transactions.append(await Transaction.from_hex(tx_hex))
                    
                    if not await create_block(block_content, final_transactions):
                        await security.reputation_manager.record_violation(
                            verified_sender, 'invalid_bulk_block', severity=7
                        )
                        return {'ok': False, 'error': f'Block {block_no} failed validation. Halting.'}
                    
                    await security.block_cache.put(block_identifier, True)
                    
                    if LOG_INCLUDE_BLOCK_SYNC_MESSAGES:
                        logger.info(f"Accepted block {block_no} from {verified_sender} via bulk sync.")

                # Reward successful bulk submission
                await security.reputation_manager.record_good_behavior(
                    verified_sender, points=len(blocks_to_process) * 2
                )

                return {'ok': True, 'result': f'Successfully processed {len(blocks_to_process)} blocks.'}
        
    except HTTPException:
        raise
    except Exception as e:
        await security.security_monitor.log_event('bulk_sync_error', {
            'peer_id': verified_sender,
            'error': str(e)
        })
        raise


@app.get("/handshake/challenge")
@limiter.limit("30/minute")
async def handshake_challenge(request: Request, pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability response")):
    """
    Initiates a handshake with another node.<br>
    
    This is the first step of the handshake protocol. The responder uses the challenge<br>
    to sign their response and prove their identity. This endpoint advertises the node's ID,<br>
    public key, whether it is public or private, URL, current height, and last block hash.<br>

    - Parameters:
      - pretty (flag): Formats JSON output for readability response.

    - Returns:
      - Response: JSON response with challenge, node_id, public key, and chain state.
    """
    challenge = await security.handshake_manager.create_challenge()

    # Get our current chain state to send to the peer.
    height = await db.get_next_block_id() - 1
    last_block = await db.get_block_by_id(height) if height > -1 else None
    
    result = {
        "ok": True, 
        "result": {
            "challenge": challenge,
            "node_id": get_node_id(),
            "pubkey": get_public_key_hex(),
            "is_public": NodesManager.self_is_public,
            "url": DENARO_SELF_URL,
            "node_version": NODE_VERSION,
            "height": height,
            "last_hash": last_block['hash'] if last_block else None
        }
    }
    
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.post("/handshake/response")
@limiter.limit("30/minute")
async def handshake_response(
    request: Request,
    body: dict = Body(...),
    verified_sender: str = Depends(get_verified_sender)
):
    """
    Authenticated endpoint that is used to complete a handshake, negotiate synchronization, and for other communications between nodes.<br>

    Verifies the peer's signature on the challenge.<br>
    Compares chain heights to determine if a sync (PUSH or PULL) is needed.<br>

    - Returns:
      - JSONResponse:
        - 200 OK with 'sync_requested' if we need the peer to push blocks to us.
        - 409 Conflict with 'sync_required' if the peer needs to pull from us.
        - 200 OK if fully synced.
            
    - Raises:
      - HTTPException: If challenge is invalid/expired or signature is missing.
    """
    if not verified_sender:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signed request required.")

    # --- Read data from request ---
    challenge = body.get('challenge')
    try:
        peer_height = int(request.headers.get('x-denaro-height', -1))
    except (ValueError, TypeError):
        peer_height = -1
    peer_hash = request.headers.get('x-denaro-last_hash')

    if not challenge:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing challenge.")

    # --- Verify challenge ---
    if not await security.handshake_manager.verify_and_consume_challenge(challenge):
        await security.reputation_manager.record_violation(verified_sender, 'invalid_handshake', severity=6)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or expired challenge.")
    
    logger.info(f"Received handshake from {verified_sender} (Height: {peer_height})")

    # --- Compare Chain States and Determine Action ---
    local_height = await db.get_next_block_id() - 1
    
    if peer_height > local_height:
        # CASE 1: We are behind.
        # We must ask the connecting peer (who has the longer chain) to PUSH blocks to us.
        # This is critical for NAT traversal (e.g., an empty public node learning from a private node).
        logger.info(f"Our chain is behind. Requesting peer {verified_sender} to PUSH-sync to us.")
        return JSONResponse(
            status_code=200, # A successful response that contains instructions
            content={
                'ok': True,
                'result': 'sync_requested', # Special status telling the client to initiate a push
                'detail': {
                    'start_block': local_height + 1,
                    'target_block': peer_height + 1
                }
            }
        )
        
    elif local_height > peer_height:
        # CASE 2: The connecting peer is behind.
        # We tell them they need to sync, and it's their responsibility to act.
        # We respond with a 409 Conflict to signal a state mismatch they need to resolve.
        logger.info(f"Our chain is longer. Informing peer {verified_sender} a sync is required.")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                'ok': False, 
                'error': 'sync_required',
                'result': {'next_block_expected': peer_height + 1}
            }
        )
    
    # CASE 3: Heights are equal. We assume they are in sync.
    # (A more advanced implementation could compare hashes here to detect forks).
    logger.info(f"Handshake complete for peer {verified_sender}. Chains appear to be in sync.")
    return {"ok": True, "result": "Handshake successful."}


@app.get("/sync_blockchain")
@limiter.limit("10/minute")
async def sync(request: Request, node_id: str = None): 
    """
    Manually triggers the blockchain synchronization process on the node.<br>
    Allows a specific peer to be specified via `node_id`.<br>

    - Parameters:
      - node_id (str, optional): Specific node ID to sync from. Defaults to random peer.

    - Returns:
      - JSONResponse: Confirmation that sync has started.
    """
    if security.sync_state_manager.is_syncing:
        return {'ok': False, 'error': 'Node is already syncing'}
    
    background_tasks = BackgroundTasks()
    background_tasks.add_task(_sync_blockchain, node_id=node_id)
    
    return JSONResponse(
        content={'ok': True, 'result': 'Synchronization process has been initiated.'},
        background=background_tasks
    )


@app.get("/get_mining_info")
@limiter.limit("60/minute")
async def get_mining_info(
    request: Request,
    background_tasks: BackgroundTasks,
    pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability"),
    debug: FlagParameter = Query(default=False, description="Include debug info about transaction selection"),
):
    """
    Generates and returns a block template that is used by miners to create new blocks.<br>
    
    Constructs a candidate block by selecting valid transactions from the mempool.<br>
    Handles dependencies (parents before children) and maximizes fee/size efficiency.<br>

    - Parameters:
      - pretty (flag): Formats JSON output for readability.
      - debug (flag): Include debug info about transaction selection.

    - Returns:
      - Response: JSON with mining template (difficulty, last_block, pending_transactions, merkle_root).
    """
    

    # Recompute difficulty/tip
    Manager.difficulty = None
    difficulty, last_block = await get_difficulty()
    
    next_block_height = (last_block['id'] + 1) if last_block else 0

    # Guard mempool size (same as before)
    pending_count = await db.get_pending_transaction_count()
    if pending_count > MAX_MEMPOOL_SIZE:
        logger.warning(f"Mempool size ({pending_count}) exceeds limit of ({MAX_MEMPOOL_SIZE}). Triggering cleanup.")
        await clear_pending_transactions([])

    # This avoids whatever filtering/ordering get_pending_transactions_limit() was doing.
    try:
        all_hashes = await db.get_all_pending_transaction_hashes()  # returns List[str]
    except Exception as e:
        logger.error(f"Error fetching mempool hashes: {e}")
        all_hashes = []

    # Optionally cap to something huge to avoid pathological mempools
    if len(all_hashes) > MAX_MINING_CANDIDATES:
        all_hashes = all_hashes[:MAX_MINING_CANDIDATES]

    # Preserve DB-provided order (whatever it is), and create a stable index
    order_index = {h: i for i, h in enumerate(all_hashes)}

    # Fetch full tx objects for these hashes
    pending_tx_objects = await db.get_pending_transactions_by_hash(all_hashes)

    # Build a hash -> Transaction map and drop unknowns (shouldn't happen, but be safe)
    tx_by_hash = {}
    for tx in pending_tx_objects:
        try:
            tx_by_hash[tx.hash()] = tx
        except Exception:
            # If deserialization happens, skip the tx
            continue

    # Only keep hashes we could hydrate
    candidate_hashes = [h for h in all_hashes if h in tx_by_hash]
    logger.debug(f"Building block template from full mempool. Candidates: {len(candidate_hashes)}")

    # Fast-path: nothing pending
    if not candidate_hashes:
        merkle_root = get_transactions_merkle_tree([], next_block_height)
        result = {
            'ok': True,
            'result': {
                'difficulty': difficulty,
                'last_block': last_block,
                'pending_transactions': [],
                'pending_transactions_hashes': [],
                'merkle_root': merkle_root,
            }
        }
        return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result

    # Debug info per tx
    debug_rows = {}  # h -> dict

    # Memoize chain prev-tx lookups
    chain_tx_cache = {}

    async def chain_tx(prev_hash: str):
        if prev_hash in chain_tx_cache:
            return chain_tx_cache[prev_hash]
        data = await db.get_nice_transaction(prev_hash)
        chain_tx_cache[prev_hash] = data
        return data

    # Dependency graph
    deps = {h: set() for h in candidate_hashes}   # child -> set(parent hashes in mempool)
    children = defaultdict(set)                   # parent -> set(children)
    prevouts = {}                                 # h -> set("txhash:index")
    invalid = set()                               # bad references / failed verify

    # First pass: validate inputs, build deps, record prevouts
    for h in candidate_hashes:
        tx = tx_by_hash[h]
        info = {
            'hash': h,
            'selected': False,
            'reason': None,
            'deps': [],
            'prevouts': [],
            'indegree_initial': None,
            'indegree_final': None,
            'parents_not_selected': [],
        }

        # Record prevouts for in-block double-spend checks
        my_prevouts = set(f"{inp.tx_hash}:{inp.index}" for inp in tx.inputs)
        prevouts[h] = my_prevouts
        info['prevouts'] = list(my_prevouts)

        try:
            ok = True
            for inp in tx.inputs:
                p = inp.tx_hash

                # If the parent is also pending, register dependency
                if p in tx_by_hash:
                    deps[h].add(p)
                    continue

                # Otherwise the parent must exist on-chain and index be valid
                src = await chain_tx(p)
                if src is None:
                    logger.debug(f"Tx {h[:10]} references unknown prev tx {p[:10]}")
                    info['reason'] = f"invalid_input_unknown_parent:{p}"
                    ok = False
                    break

                if 'outputs' in src and inp.index >= len(src['outputs']):
                    logger.debug(f"Tx {h[:10]} references out-of-range output {inp.index} in {p[:10]}")
                    info['reason'] = f"invalid_input_out_of_range:{p}:{inp.index}"
                    ok = False
                    break

            if not ok:
                invalid.add(h)
                debug_rows[h] = info
                continue

            # Full signature/script verification
            if not await tx.verify():
                logger.debug(f"Tx {h[:10]} failed verification")
                info['reason'] = "verify_failed"
                invalid.add(h)
                debug_rows[h] = info
                continue

        except Exception as e:
            logger.debug(f"Tx {h[:10]} verification error: {e}")
            info['reason'] = f"verify_exception:{e}"
            invalid.add(h)
            debug_rows[h] = info
            continue

        info['deps'] = list(deps[h])
        debug_rows[h] = info

    # Build children edges (ignore invalid parents)
    for child_h, parents in deps.items():
        if child_h in invalid:
            continue
        for parent_h in parents:
            if parent_h not in invalid:
                children[parent_h].add(child_h)

    # Compute indegrees
    indegree = {}
    for h in candidate_hashes:
        if h in invalid:
            continue
        indegree[h] = sum(1 for p in deps[h] if p not in invalid)
        debug_rows[h]['indegree_initial'] = indegree[h]

    # Queue of zero-dep txs in stable order (DB order)
    zero_dep = [h for h, d in indegree.items() if d == 0]
    zero_dep.sort(key=lambda k: order_index.get(k, 1_000_000))
    queue = deque(zero_dep)

    # Selection loop
    selected = []
    selected_hashes = set()
    spent_prevouts = set()
    total_size = 0
    size_hard_stop = False

    while queue:
        h = queue.popleft()
        if h in selected_hashes or h in invalid:
            continue

        tx = tx_by_hash[h]
        tx_hex = tx.hex()

        # Ensure we don't exceed size target
        if total_size + len(tx_hex) > MAX_TX_DATA_SIZE:
            logger.debug("Reached MAX_TX_DATA_SIZE while assembling block template.")
            size_hard_stop = True
            break

        # In-block double-spend check
        if not prevouts[h].isdisjoint(spent_prevouts):
            if debug:
                debug_rows[h]['reason'] = "double_spend_conflict_in_block"
            continue

        # Accept tx
        selected.append(tx)
        selected_hashes.add(h)
        spent_prevouts.update(prevouts[h])
        total_size += len(tx_hex)
        debug_rows[h]['selected'] = True

        # Relax children indegrees
        for c in children.get(h, ()):
            if c in indegree:
                indegree[c] -= 1
                if indegree[c] == 0:
                    queue.append(c)

    # Annotate non-selected reasons
    for h, d in indegree.items():
        debug_rows[h]['indegree_final'] = d
        if not debug_rows[h]['selected'] and h not in invalid and debug_rows[h]['reason'] is None:
            if d > 0:
                blocked_by = [p for p in deps[h] if p not in invalid and p not in selected_hashes]
                debug_rows[h]['reason'] = "blocked_by_unselected_parents"
                debug_rows[h]['parents_not_selected'] = blocked_by
            else:
                if size_hard_stop:
                    debug_rows[h]['reason'] = "size_limit_reached"
                else:
                    debug_rows[h]['reason'] = "skipped_for_conflict_or_unknown"

    # Purge invalids
    if invalid:
        logger.info(f"Removing {len(invalid)} invalid tx(s) from mempool")
        for h in invalid:
            try:
                await db.remove_pending_transaction(h)
                await security.transaction_pool.remove_transactions([h])
            except Exception as e:
                logger.error(f"Error removing invalid tx {h[:10]}: {e}")

    # Compose response
    selected_hex = [tx.hex() for tx in selected]
    selected_hashes_list = [tx.hash() for tx in selected]
    merkle_root = get_transactions_merkle_tree(selected_hashes_list, next_block_height)
    
    # Periodic cleanup (unchanged)
    if LAST_PENDING_TRANSACTIONS_CLEAN[0] < timestamp() - 600:
        logger.debug("Clearing old pending transactions")
        LAST_PENDING_TRANSACTIONS_CLEAN[0] = timestamp()
        # Feed the cleaner the full set we loaded (not just selected)
        try:
            # If you want to pass hex, hydrate:
            all_loaded_hex = [tx_by_hash[h].hex() for h in candidate_hashes]
            background_tasks.add_task(clear_pending_transactions, all_loaded_hex)
        except Exception:
            pass

    payload = {
        'ok': True,
        'result': {
            'difficulty': difficulty,
            'last_block': last_block,
            'pending_transactions': selected_hex,
            'pending_transactions_hashes': selected_hashes_list,
            'merkle_root': merkle_root,
        }
    }

    if debug:
        dbg = []
        for h, row in debug_rows.items():
            dbg.append({
                'hash': h,
                'selected': row['selected'],
                'reason': row['reason'],
                'deps': row['deps'],
                'prevouts': row['prevouts'],
                'indegree_initial': row['indegree_initial'],
                'indegree_final': row['indegree_final'],
                'parents_not_selected': row['parents_not_selected'],
            })
        payload['debug'] = dbg

    return Response(content=json.dumps(payload, indent=4, cls=CustomJSONEncoder), media_type="application/json") if pretty else payload



@app.get("/get_address_info")
@limiter.limit("8/second")
async def get_address_info(
    request: Request, 
    address: str = Query(..., description="The wallet address to query"),
    transactions_count_limit: int = Query(default=5, le=50, description="Number of transactions per page (max 50)"), 
    page: int = Query(default=1, ge=1, description="Page number for pagination"),
    show_pending: FlagParameter = Query(default=False, description="Include pending (mempool) transactions"),
    verify: FlagParameter = Query(default=False, description="Perform extra verification on returned transactions"),
    pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability"),
):
    """
    Returns balance and transaction history for a specific address.<br>
    
    - Parameters:
      - address (str): The wallet address to query.
      - transactions_count_limit (int): Number of transactions per page (max 50).
      - page (int): Page number for pagination.
      - show_pending (flag): Include pending (mempool) transactions.
      - verify (flag): Perform extra verification on returned transactions.
      - pretty (flag): Formats JSON output for readability.

    - Returns:
      - Response: JSON with balance, spendable outputs, and transaction history.

    - Raises:
      - HTTPException: If address format is invalid or rate limit (query cost) exceeded.
    """
    
    # Validate address format
    if not security.input_validator.validate_address(address):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid address format")
    
    # Check query cost
    offset = (page - 1) * transactions_count_limit
    await security.query_calculator.check_and_update_cost(
        address, offset, transactions_count_limit
    )
    
    outputs = await db.get_spendable_outputs(address)
    balance = sum(output.amount for output in outputs)
    
    transactions = await db.get_address_transactions(
        address, limit=transactions_count_limit, 
        offset=offset, check_signatures=True
    ) if transactions_count_limit > 0 else []

    result = {'ok': True, 'result': {
        'balance': "{:f}".format(balance),
        'spendable_outputs': [
            {'amount': "{:f}".format(output.amount), 
             'tx_hash': output.tx_hash, 
             'index': output.index} 
            for output in outputs
        ],
        'transactions': [
            await db.get_nice_transaction(tx.hash(), address if verify else None) 
            for tx in transactions
        ],
        'pending_transactions': [
            await db.get_nice_transaction(tx.hash(), address if verify else None) 
            for tx in await db.get_address_pending_transactions(address, True)
        ] if show_pending else None,
        'pending_spent_outputs': await db.get_address_pending_spent_outputs(address) if show_pending else None
    }}
    
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.get("/get_pending_transactions")
@limiter.limit("8/second")
async def get_pending_transactions(request: Request, pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability")):
    """
    Returns the hex representation of transactions in the local mempool.<br>
    
    Data is limited to the first 1024 transactions to prevent huge responses.<br>

    - Parameters:
      - pretty (flag): Formats JSON output for readability.

    - Returns:
      - Response: JSON list of transaction hex strings.
    """
    result = {'ok': True, 'result': [tx.hex() for tx in await db.get_pending_transactions_limit(1024)]}
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.get("/get_transaction")
@limiter.limit("8/second")
async def get_transaction(request: Request,
                          hash: str = Query(..., description="The 64-char hex hash of the transaction"),
                          pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability")):
    """
    Returns a specific transaction by its hash.<br>
    
    - Parameters:
      - hash (str): The 64-char hex hash of the transaction.
      - pretty (flag): Formats JSON output for readability.

    - Returns:
      - Response: JSON with transaction data or error if not found.

    - Raises:
      - HTTPException: If hash format is invalid.
    """
    # Validate transaction hash format
    if not security.input_validator.validate_hex(hash, min_length=64, max_length=64):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid transaction hash format")
    tx = await db.get_nice_transaction(hash)
    if tx is None:
        result = {'ok': False, 'error': 'Not found'}
    else:
        result = {'ok': True, 'result': tx}
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.get("/get_block")
@limiter.limit("60/minute")
async def get_block(
    request: Request, 
    id: Optional[int] = Query(default=None, description="Block height (integer)"),
    hash: Optional[str] = Query(default=None, description="Block Hash (hex string)"),
    full_transactions: FlagParameter = Query(default=False, description="Returns full transaction objects instead of just hex/hashes"),
    pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability"),
):
    """
    Returns a block by its ID (height) or hash.<br>
        
    - Parameters:
      - id (int): Block height (integer).
      - hash (str): Block Hash (hex string).
      - full_transactions (flag): Returns full transaction objects instead of just hex/hashes.
      - pretty (flag): Formats JSON output for readability.

    - Returns:
      - Response: JSON with block header and transaction data.

    - Raises:
      - HTTPException: If id or hash format is invalid.
    """
    
    # Validate block parameter - must provide either id or hash
    if id is None and not hash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Must provide either 'id' (block height) or 'hash' (block hash)")
    
    block_info = None
    block_hash = None

    if id is not None:
        if not await security.input_validator.validate_block_height(id, db, max_ahead=0):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid block height")
        
        block_info = await db.get_block_by_id(id)
        if block_info:
            block_hash = block_info['hash']
            
    else:
        if not security.input_validator.validate_hex(hash, min_length=64, max_length=64):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid block hash format")
        
        block_hash = hash
        block_info = await db.get_block(block_hash)
        
    if block_info:
        result = {'ok': True, 'result': {
            'block': block_info,
            'transactions': await db.get_block_transactions(block_hash, hex_only=True) if not full_transactions else None,
            'full_transactions': await db.get_block_nice_transactions(block_hash) if full_transactions else None
        }}
    else:
        result = {'ok': False, 'error': 'Not found'}
    
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.get("/get_blocks")
@limiter.limit("60/minute")
async def get_blocks(
    request: Request, 
    offset: int = Query(default=..., ge=0, description="Starting block height/index"), 
    limit: int = Query(default=..., le=512, description="Number of blocks to retrieve (max 512)"),
    pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability"),
):
    """
    Returns a range of blocks.<br>
    
    - Parameters:
      - offset (int): Starting block height/index.
      - limit (int): Number of blocks to retrieve (max 512).
      - pretty (flag): Formats JSON output for readability.

    - Returns:
      - Response: JSON list of blocks.
    """
    # Use QueryCostCalculator to prevent abuse of pagination
    #client_ip = get_remote_address(request)
    #await security.query_calculator.check_and_update_cost(client_ip, offset, limit)

    blocks = await db.get_blocks(offset, limit)
    result = {'ok': True, 'result': blocks}
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.get("/get_status")
@limiter.limit("60/minute")
async def get_status(request: Request, pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability")):
    """
    Returns the general status of the node including blockchain state and node metadata.<br>
    
    - Parameters:
      - pretty (flag): Formats JSON output for readability.

    - Returns:
      - Response: JSON object with node_id, pubkey, height, last_block_hash, is_public, url, 
                  node_type, uptime_seconds, and sync_status.
    """
    try:
        height = await db.get_next_block_id() - 1
        # Create a base response object that always includes the node's ID.
        response_data = {
            'node_id': self_node_id,
            'pubkey': get_public_key_hex(),
            'url': DENARO_SELF_URL,
            'is_public': self_is_public,
            "node_version": NODE_VERSION,
            'height': height,
            'last_block_hash': None,
            'uptime_seconds': int(time.time() - startup_time),
        }

        if height >= 0:
            last_block = await db.get_block_by_id(height)
            if last_block:
                # If a block exists, add its hash to the response.
                response_data['last_block_hash'] = last_block['hash']
            else:
                # This handles an edge case where the DB might be inconsistent.
                # We report height as -1 to signal a non-ready state.
                response_data['height'] = -1

        result = {'ok': True, 'result': response_data}

    except Exception as e:
        logger.error(f"Error in /get_status: {e}")
        await security.security_monitor.log_event('get_status_error', {'error': str(e)})
        result = {'ok': False, 'error': 'Internal server error'}
    
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.api_route("/get_peers", methods=["GET", "POST"])
@limiter.limit("60/minute")
async def get_peers(
    request: Request,
    private: FlagParameter = Query(default=False, description="Only return private peers"),
    public: FlagParameter = Query(default=False, description="Only return public peers"),
    show_banned: FlagParameter = Query(default=False, description="Include banned peers"),
    show_stats: FlagParameter = Query(default=False, description="Include peer statistics"),
    pretty: FlagParameter = Query(default=False, description="Formats JSON output for readability"),
):
    """
    Returns a list of active peers, peer statistics, and reputation status.<br>

    - Parameters:
      - private (flag): Only return private peers.
      - public (flag): Only return public peers.
      - show_banned (flag): Include banned peers.
      - show_stats (flag): Include peer statistics.
      - pretty (flag): Formats JSON output for readability.

    - Returns:
      - dict: Contains a list of peer objects (node_id, pubkey, url, last_seen timestamp).<br>
              If show_stats is present, each peer object also includes reputation_score and is_banned,<br>
              and the response includes peer_stats with total_peers, public_peers, private_peers,<br>
              connectable_peers, and recent_peers.<br>
    """
    peers_list = []
    for peer_id, peer_data in NodesManager.active_peers.items():

        # Filter by public/private based on query parameters
        # If only public is specified, only return public peers
        if public and not private:
            if not peer_data.get('is_public'):
                continue

        # If only private is specified, only return private peers
        elif private and not public:
            if peer_data.get('is_public'):
                continue

        # Only return peers that haven't been banned
        is_banned = await security.reputation_manager.is_banned(peer_id)
        if is_banned and not show_banned:
            continue
        
        reputation_score = await security.reputation_manager.get_score(peer_id)

        peer_url = peer_data.get('url')

        peer_version = peer_data.get('node_version')
        
        peer_info = {
            'node_id': peer_id,
            'pubkey': peer_data.get('pubkey'),
            'url': peer_url if peer_url else None,
            'is_public': peer_data.get('is_public'),
            'node_version': peer_version if peer_version else None,
            'reputation_score': reputation_score        
        }

        if show_banned:
            peer_info['is_banned'] = is_banned  
        
        peer_info['last_seen'] = peer_data.get('last_seen')

        peers_list.append(peer_info)
    
    result = {"ok": True, "result": {"peers": peers_list}}
    
    # Include peer statistics if requested
    if show_stats:
        try:
            # Get peer information and statistics (includes all peers, even banned ones for stats)
            all_peers = NodesManager.get_all_peers()
            
            peer_stats = {
                'total_peers': len(all_peers),
                'public_peers': len([p for p in all_peers if p.get('is_public', False)]),
                'private_peers': len([p for p in all_peers if not p.get('is_public', False)]),
                'connectable_peers': len([p for p in all_peers if p.get('url')]),
                'recent_peers': len([p for p in all_peers if time.time() - p.get('last_seen', 0) < 3600])
            }
            
            result["result"]["peer_stats"] = peer_stats
        except Exception as e:
            logger.error(f"Error calculating peer stats in /get_peers: {e}")
    
    return Response(content=json.dumps(result, indent=2, cls=CustomJSONEncoder), media_type="application/json") if pretty else result


@app.post("/check_reachability")
@limiter.limit("2/minute")
async def check_reachability(
    request: Request,
    body: dict = Body(...),
    verified_sender: str = Depends(get_verified_sender)
):
    """
    Checks if a given URL is reachable from this node.<br>
    
    Used by peers to verify their own public reachability (NAT traversal check).<br>
    Secured to prevent SSRF: only allows global IPs and caches results.<br>

    - Returns:
      - dict: {'ok': True, 'result': {'reachable': bool, 'cached': bool}}
    """
    if not verified_sender:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signed request required.")

    url_to_check = body.get('url_to_check')
    if not url_to_check:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must contain 'url_to_check'.")

    cached_result = await security.reachability_cache.get(url_to_check)
    if cached_result is not None:
        logger.debug(f"Returning cached reachability for {url_to_check} for peer {verified_sender}")
        return {"ok": True, "result": {"reachable": cached_result, "cached": True}}

    is_valid, resolved_ip = await security.dns_client.validate_and_resolve(url_to_check)
    if not is_valid:
        return {"ok": False, "error": "Invalid or unresolvable URL"}

    ip_obj = ipaddress.ip_address(resolved_ip)
    if not ip_obj.is_global:
        return {"ok": False, "error": "IP address is not globally routable and cannot be checked."}

    is_reachable = False
    try:
        # Use the single, shared, and persistent http_client
        response = await http_client.get(url_to_check)
        if response.status_code > 0:
            is_reachable = True
    except httpx.RequestError as e:
        logger.warning(f"Reachability check failed for {url_to_check}: {e}")
        is_reachable = False

    await security.reachability_cache.put(url_to_check, is_reachable)
    return {"ok": True, "result": {"reachable": is_reachable, "cached": False}}


@app.post("/get_mempool_hashes")
async def get_mempool_hashes(verified_sender: str = Depends(get_verified_sender)):
    """
    Authenticated endpoint to retrieve a list of all transaction hashes that are currently in the mempool.<br>
    
    - Parameters:
      - pretty (bool): Whether to pretty-print JSON.

    - Returns:
      - Response: JSON list of transaction hashes.
    """
    if not verified_sender:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signed request required.")
    
    hashes = await db.get_all_pending_transaction_hashes()
    return {"ok": True, "result": hashes}


@app.post("/get_transactions_by_hash")
@limiter.limit("20/minute")
async def get_transactions_by_hash(
    request: Request,
    body: dict = Body(...),
    verified_sender: str = Depends(get_verified_sender)
):
    """
    Authenticated endpoint to retrieve full transaction data for a list of hashes.<br>
    
    - Returns:
      - dict: List of transaction hex strings.
        
    - Raises:
      - HTTPException: If list is missing, too large, or contains invalid hashes.
    """
    if not verified_sender:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signed request required.")
    
    hashes_to_find = body.get('hashes')
    if not isinstance(hashes_to_find, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must contain a 'hashes' list.")
    
    if len(hashes_to_find) > MAX_TX_FETCH_LIMIT:
        await security.reputation_manager.record_violation(
            verified_sender, 'tx_fetch_limit_exceeded', severity=2
        )
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, 
            f"Request limit exceeded. Maximum {MAX_TX_FETCH_LIMIT} transactions per request."
        )

    # Validate all hashes using the secure validator
    for hash_str in hashes_to_find:
        if not security.input_validator.validate_hex(hash_str, min_length=64, max_length=64):
            await security.reputation_manager.record_violation(
                verified_sender, 'invalid_tx_hash_format', severity=3
            )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid transaction hash format for hash: {hash_str}")

    found_txs = await db.get_pending_transactions_by_hash(hashes_to_find)
    tx_hex_list = [tx.hex() for tx in found_txs]
    
    return {"ok": True, "result": tx_hex_list}


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            # Format Decimal as a string to preserve precision
            return "{:f}".format(o)
        if isinstance(o, datetime):
            # Format datetime to a standard ISO string
            return o.isoformat()
        return super().default(o)