# denaro/node/nodes_manager.py

import os
import json
import time
from os.path import dirname, exists
from random import sample
from typing import Optional, List, Any

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse, urlencode

import httpx

from ..constants import (
    ACTIVE_NODES_DELTA, MAX_PEERS_COUNT, DENARO_SELF_URL,
    LOG_INCLUDE_REQUEST_CONTENT, LOG_INCLUDE_RESPONSE_CONTENT, LOG_MAX_PATH_LENGTH,
    NODE_VERSION
)

from .identity import get_node_id, get_public_key_hex, sign_message, get_canonical_json_bytes
from ..logger import get_logger

logger = get_logger(__name__)

# --- Constants ---
path = dirname(os.path.realpath(__file__)) + '/nodes.json'

class NodesManager:
    db_path = path
    self_id: str = None
    peers: dict = None
    self_is_public: bool = False

    # The self-contained httpx.AsyncClient has been REMOVED from this class.
    # It will now be passed in from the main application.

    @staticmethod
    def init(self_node_id: str):
        """Initializes the manager, loading peers from the JSON file."""
        NodesManager.self_id = self_node_id
        if not exists(NodesManager.db_path):
            NodesManager.purge_peers() # Corrected from self.purge_peers()
        else:
            with open(NodesManager.db_path, 'r') as f:
                data = json.load(f)
                NodesManager.peers = data.get('peers', {})

    @staticmethod
    def purge_peers():
        """Clears the peer list and resets the JSON file."""
        NodesManager.peers = {}
        NodesManager.sync()
    
    @staticmethod
    def sync():
        """Saves the current peer list to the JSON file."""
        with open(NodesManager.db_path, 'w') as f:
            json.dump({'peers': NodesManager.peers}, f, indent=4)

    @staticmethod
    async def request(client: httpx.AsyncClient, url: str, method: str = 'GET', signed: bool = False, node_id: Optional[str] = None, **kwargs):
        """
        A wrapper for making async HTTP requests.
        It now re-raises RequestError so the caller can handle unreachability,
        while gracefully handling other response errors.
        """
        start_time = time.time()
        
        # Build the URL with params for logging
        log_url = url
        if 'params' in kwargs and kwargs['params']:
            params_str = urlencode(kwargs['params'], doseq=True)
            separator = '&' if '?' in url else '?'
            log_url = f"{url}{separator}{params_str}"
        
        # Truncate very long URLs to prevent log spam
        if len(log_url) > LOG_MAX_PATH_LENGTH:
            log_url = log_url[:LOG_MAX_PATH_LENGTH] + "...[TRUNCATED]"
        
        signed_marker = " [SIGNED]" if signed else ""
        
        # Log request body if present and has content
        body = None
        if LOG_INCLUDE_REQUEST_CONTENT:
            for body_key in ('content', 'content_body', 'data', 'json'):
                if body_key not in kwargs:
                    continue
                
                value = kwargs[body_key]
                if value is None:
                    continue
                
                # Normalize value: parse JSON strings once, keep other types as-is
                parsed_value = None
                if isinstance(value, str):
                    if len(value) == 0:
                        continue
                    # Try to parse as JSON
                    try:
                        parsed_value = json.loads(value)
                    except (json.JSONDecodeError, ValueError):
                        parsed_value = value  # Not JSON, use string as-is
                elif isinstance(value, (dict, list)):
                    parsed_value = value
                else:
                    parsed_value = value
                
                # Skip empty collections
                if isinstance(parsed_value, (dict, list)) and len(parsed_value) == 0:
                    continue
                
                # Format the body nicely
                if isinstance(parsed_value, (dict, list)):
                    body = json.dumps(parsed_value, indent=2)
                elif isinstance(parsed_value, str):
                    body = parsed_value
                else:
                    body = str(parsed_value)
                
                body = f"\n\nOutgoing Request:\n\"{body}\"\n"
                break
            
        logger.info(f"--> \"{method} {log_url} HTTP/1.1\"{signed_marker}{body if body else ''}")
        
        try:
            response = await client.request(method, url, **kwargs)
            process_time = time.time() - start_time
            status_code = response.status_code
            
            # Prevents 409 from being treated as an error
            # It's a hint from the peer that we are out of sync
            if response.status_code != 409:
                response.raise_for_status()
            
            # Extract and format response body if present
            response_body = None
            if LOG_INCLUDE_RESPONSE_CONTENT:
                try:
                    response_text = response.text
                    if response_text:
                        try:
                            parsed_value = json.loads(response_text)
                            # Skip empty collections
                            if isinstance(parsed_value, (dict, list)) and len(parsed_value) == 0:
                                response_body = None
                            else:
                                # Format as pretty JSON
                                response_body = json.dumps(parsed_value, indent=2)
                        except (json.JSONDecodeError, ValueError):
                            # Not JSON, use raw string
                            if len(response_text) > 0:
                                response_body = response_text
                except Exception:
                    # Silently fail response body extraction
                    response_body = None
            
            response_body_log = f"\n\nIncoming Response:\n\"{response_body}\"\n" if response_body else ""
            logger.info(f"<-- \"{method} {log_url} HTTP/1.1\" {status_code}⁢ ({process_time:.3f}s){response_body_log}")
            return response.json()
        
        except httpx.RequestError as e:
            process_time = time.time() - start_time
            logger.warning(f"<-- \"{method} {log_url} HTTP/1.1\" NETWORK_ERROR ({process_time:.3f}s)")
            raise e

        except (json.JSONDecodeError, httpx.HTTPStatusError) as e:
            process_time = time.time() - start_time
            status_code = getattr(e, 'response', None)
            status_code = status_code.status_code if status_code else ''
            logger.warning(f"<-- \"{method} {log_url} HTTP/1.1\" {status_code}⁢ ERROR ({process_time:.3f}s): {e}")
            return None

    @staticmethod
    def add_or_update_peer(node_id: str, pubkey: str, url: str | None, is_public: bool, version: str | None = None):
        """
        Adds a new peer or updates an existing one's information.
        """
        if node_id == NodesManager.self_id:
            return False
    
        is_new = node_id not in NodesManager.peers
        if is_new and len(NodesManager.peers) >= MAX_PEERS_COUNT:
            logger.warning(f"Peer limit reached ({MAX_PEERS_COUNT}), new peer will not be added.")
            return False
    
        url_to_store = url.strip('/') if url else None
        
        NodesManager.peers[node_id] = {
            'pubkey': pubkey,
            'url': url_to_store,
            'last_seen': int(time.time()),
            'is_public': is_public,
            'node_version' : version
        }
        NodesManager.sync()
        return is_new
    
    @staticmethod
    def update_peer_last_seen(node_id: str):
        """
        Updates the 'last_seen' timestamp for an active peer.
        """
        peer = NodesManager.peers.get(node_id)
        if peer:
            peer['last_seen'] = int(time.time())
            NodesManager.sync()
    
    @staticmethod
    def get_peer(node_id: str) -> dict:
        """Retrieves a peer's data by their NodeID."""
        return NodesManager.peers.get(node_id)
        
    @staticmethod
    def get_all_peers() -> list[dict]:
        """Returns a list of all peers, with NodeID included in each dict."""
        return [
            {'node_id': node_id, **peer_data}
            for node_id, peer_data in NodesManager.peers.items()
        ]
    
    @staticmethod
    def get_recent_nodes() -> list[dict]: # Return the full peer object
        """
        Gets a list of recently active peers.
        """
        now = int(time.time())
        all_peers = NodesManager.get_all_peers()

        active_peers = [
            peer for peer in all_peers
            if peer['last_seen'] > now - ACTIVE_NODES_DELTA
        ]
        
        active_peers.sort(key=lambda p: p['last_seen'], reverse=True)
        return active_peers
    
    @staticmethod
    def get_propagate_peers(limit: int = 10) -> list[dict]:
        """
        Gets a list of active peer objects to propagate messages to.
        """
        now = int(time.time())
        all_peers_with_id = NodesManager.get_all_peers()

        active_and_connectable_peers = [
            peer for peer in all_peers_with_id
            if peer['last_seen'] > now - ACTIVE_NODES_DELTA and peer.get('url')
        ]
        
        if len(active_and_connectable_peers) <= limit:
            return active_and_connectable_peers
        return sample(active_and_connectable_peers, k=limit)

    @staticmethod
    def set_public_status(is_public: bool):
        """Allows the main application to set the node's public status."""
        NodesManager.self_is_public = is_public

    @staticmethod
    def remove_peer(node_id: str):
        """Removes a peer from the list and syncs the changes to the JSON file."""
        if node_id in NodesManager.peers:
            del NodesManager.peers[node_id]
            NodesManager.sync()
            return True
        return False
    
    @staticmethod
    def find_peer_by_url(url: str) -> Optional[str]:
        """Finds a peer's node_id by their URL."""
        if not url:
            return None
        for node_id, peer_data in NodesManager.peers.items():
            if peer_data.get('url') == url:
                return node_id
        return None

class NodeInterface:
    def __init__(self, url: str, client: httpx.AsyncClient, db):
        self.url = url.strip('/')
        self.client = client  # Store the shared client instance
        self.db = db          # Store the shared database connection instance

    async def _signed_request(self, path: str, data: dict = {}, method: str = 'POST', signed_headers_data: dict = None) -> Optional[Any]:
        """
        Creates and sends a cryptographically signed request.
        This now includes timestamp and nonce for replay protection.
        An optional 'signed_headers_data' dict can be provided to include additional
        data in the signature (like chain state) that isn't part of the main body.
        """
        current_time = int(time.time())
        nonce = os.urandom(16).hex()
        
        # The body of the request is the 'data' dictionary serialized to a string.
        body_str = json.dumps(data)
        
        # The payload to be signed includes the body, timestamp, nonce, and any extra header data.
        payload_to_sign = {
            "body": body_str,
            "timestamp": current_time,
            "nonce": nonce
        }
        if signed_headers_data:
            payload_to_sign.update(signed_headers_data)

        canonical_bytes_to_sign = get_canonical_json_bytes(payload_to_sign)
        signature = sign_message(canonical_bytes_to_sign)
        
        headers = {
            'x-node-id': get_node_id(),
            'x-public-key': get_public_key_hex(),
            'x-signature': signature,
            'x-timestamp': str(current_time),
            'x-nonce': nonce,
            'x-node-version': NODE_VERSION,
            'Content-Type': 'application/json'
        }
        
        # Add the extra data to the headers so the recipient can verify the signature
        if signed_headers_data:
            for key, value in signed_headers_data.items():
                headers[f'x-denaro-{key}'] = str(value)
        
        should_advertise = False
        if DENARO_SELF_URL:
            if not await self.is_url_local(DENARO_SELF_URL):
                should_advertise = True
            elif await self.is_url_local(self.url):
                should_advertise = True

        if should_advertise:
            headers['x-peer-url'] = DENARO_SELF_URL
        
        full_url = f'{self.url}/{path}'
        result = await NodesManager.request(self.client, full_url, method=method, content=body_str, headers=headers, signed=True)
        return result

    async def is_url_local(self, url: str) -> bool:
        """Resolves a URL's hostname and returns True if the IP is private/local."""
        try:
            parsed_url = urlparse(url)
            hostname = parsed_url.hostname
            if not hostname: return False
            addr_info = await asyncio.get_event_loop().getaddrinfo(hostname, None, family=socket.AF_INET)
            ip_str = addr_info[0][4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
        except (socket.gaierror, ValueError, IndexError):
            return False

    # --- All following methods are now simplified to use self.client via _signed_request ---

    async def push_tx(self, tx_hex: str):
        return await self._signed_request('push_tx', {'tx_hex': tx_hex})
    
    async def push_block(self, block_data: dict):
        return await self._signed_request('push_block', block_data)

    async def push_blocks(self, blocks_payload: list):
        return await self._signed_request("push_blocks", blocks_payload)
    
    async def get_block(self, block: str):
        return await NodesManager.request(self.client, f'{self.url}/get_block', params={'block': block})
        
    async def get_blocks(self, offset: int, limit: int):
        return await NodesManager.request(self.client, f'{self.url}/get_blocks', params={'offset': offset, 'limit': limit})

    async def get_status(self):
        return await NodesManager.request(self.client, f'{self.url}/get_status')

    async def get_peers(self):
        return await NodesManager.request(self.client, f'{self.url}/get_peers', method='GET')

    async def handshake_challenge(self):
        """Initiates a handshake by asking for a challenge. Unsigned request."""
        return await NodesManager.request(self.client, f'{self.url}/handshake/challenge')

    async def handshake_response(self, challenge: str):
        """
        Responds to a challenge to prove identity. This is now a signed request
        that also includes our own chain state in the headers for negotiation.
        """
        # Get our node's current chain state from the database.
        current_height = await self.db.get_next_block_id() - 1
        last_block_hash = None
        if current_height > -1:
            last_block = await self.db.get_block_by_id(current_height)
            if last_block:
                last_block_hash = last_block['hash']

        # This data will be added to the signature and the request headers.
        our_state = {
            'height': current_height,
            'last_hash': last_block_hash
        }

        # The main body of the request only needs the challenge.
        payload = {'challenge': challenge}
        
        return await self._signed_request('handshake/response', data=payload, signed_headers_data=our_state)

    async def check_peer_reachability(self, url_to_check: str) -> bool:
        payload = {'url_to_check': url_to_check}
        resp = await self._signed_request('check_reachability', data=payload)
        
        if resp and resp.get('ok'):
            return resp.get('result', {}).get('reachable', False)
        return False
        
    async def get_mempool_hashes(self) -> Optional[dict]:
        return await self._signed_request('get_mempool_hashes')

    async def get_transactions_by_hash(self, hashes: List[str]) -> Optional[dict]:
        payload = {'hashes': hashes}
        return await self._signed_request('get_transactions_by_hash', payload)