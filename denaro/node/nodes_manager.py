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
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values

from ..constants import MAX_BLOCK_SIZE_HEX
from .identity import get_node_id, get_public_key_hex, sign_message, get_canonical_json_bytes

# --- Constants ---
ACTIVE_NODES_DELTA = 60 * 60 * 24 * 7  # 7 days
MAX_PEERS_COUNT = 200

path = dirname(os.path.realpath(__file__)) + '/nodes.json'
config = dotenv_values(".env")

DENARO_BOOTSTRAP_NODE_URL = config.get("DENARO_BOOTSTRAP_NODE", "")
SELF_URL = config.get("DENARO_SELF_URL")

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
    async def request(client: httpx.AsyncClient, url: str, method: str = 'GET', **kwargs):
        """
        A wrapper for making async HTTP requests.
        It now re-raises RequestError so the caller can handle unreachability,
        while gracefully handling other response errors.
        """
        try:
            response = await client.request(method, url, **kwargs)
            
            if response.status_code != 409:
                response.raise_for_status()
            
            return response.json()
        
        except httpx.RequestError as e:
            print(f"Network error during request to {url}: {e}")
            raise e

        except (json.JSONDecodeError, httpx.HTTPStatusError) as e:
            print(f"Request to {url} failed with a non-network error: {e}")
            return None

    @staticmethod
    def add_or_update_peer(node_id: str, pubkey: str, url: str | None, is_public: bool):
        """
        Adds a new peer or updates an existing one's information.
        """
        if node_id == NodesManager.self_id:
            return False
    
        is_new = node_id not in NodesManager.peers
        if is_new and len(NodesManager.peers) >= MAX_PEERS_COUNT:
            print("Peer limit reached, not adding new peer.")
            return False
    
        url_to_store = url.strip('/') if url else None
        
        NodesManager.peers[node_id] = {
            'pubkey': pubkey,
            'url': url_to_store,
            'last_seen': int(time.time()),
            'is_public': is_public
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
            'Content-Type': 'application/json'
        }
        
        # Add the extra data to the headers so the recipient can verify the signature
        if signed_headers_data:
            for key, value in signed_headers_data.items():
                headers[f'x-denaro-{key}'] = str(value)
        
        should_advertise = False
        if SELF_URL:
            if not await self.is_url_local(SELF_URL):
                should_advertise = True
            elif await self.is_url_local(self.url):
                should_advertise = True

        if should_advertise:
            headers['x-peer-url'] = SELF_URL

        return await NodesManager.request(
            self.client, f'{self.url}/{path}', method=method, content=body_str, headers=headers
        )

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
    
    async def submit_block(self, block_data: dict):
        return await self._signed_request('submit_block', block_data)

    async def submit_blocks(self, blocks_payload: list):
        return await self._signed_request("submit_blocks", blocks_payload)
    
    async def get_block(self, block: str):
        return await NodesManager.request(self.client, f'{self.url}/get_block', params={'block': block})
        
    async def get_blocks(self, offset: int, limit: int):
        return await NodesManager.request(self.client, f'{self.url}/get_blocks', params={'offset': offset, 'limit': limit})

    async def get_status(self):
        return await NodesManager.request(self.client, f'{self.url}/get_status')

    async def get_peers(self):
        return await self._signed_request('get_peers', method='POST')

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