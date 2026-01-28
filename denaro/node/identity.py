# denaro/node/identity.py

import os
import hashlib
import json # Import json for canonical serialization
from fastecdsa import keys, ecdsa
from denaro.constants import CURVE, KEY_FILE_PATH
from ..logger import get_logger

logger = get_logger(__name__)

# Define the path for the node's private key file

# --- Private variables to hold the loaded key and derived IDs ---
_private_key: int = None
_public_key = None
_node_id: str = None

def generate_new_key():
    """Generates a new P256 private key and its corresponding public key."""
    return keys.gen_keypair(CURVE)

def save_key(key: int):
    """Saves the private key (an integer) to the key file."""
    with open(KEY_FILE_PATH, 'w') as f:
        f.write(str(key))
    logger.info(f"New node identity created and saved to {KEY_FILE_PATH}")

def load_key() -> int:
    """Loads the private key from the key file."""
    if not os.path.exists(KEY_FILE_PATH):
        return None
    with open(KEY_FILE_PATH, 'r') as f:
        key_int = int(f.read().strip())
        return key_int

def initialize_identity():
    """
    The main initialization function. It loads an existing key or creates a new one,
    then populates the global identity variables.
    """
    global _private_key, _public_key, _node_id
    
    priv_key_int = load_key()
    if priv_key_int is None:
        logger.info("No node key found. Generating a new identity...")
        priv_key_int, _ = generate_new_key()
        save_key(priv_key_int)
    
    _private_key = priv_key_int
    _public_key = keys.get_public_key(_private_key, CURVE)
    
    # The Node ID is the SHA256 hash of the public key bytes (uncompressed format)
    # fastecdsa uses X and Y coordinates for the public key. We'll concatenate them.
    pubkey_bytes = _public_key.x.to_bytes(32, 'big') + _public_key.y.to_bytes(32, 'big')
    _node_id = hashlib.sha256(pubkey_bytes).hexdigest()
    
    logger.info(f"Node Identity Initialized. Node ID: {_node_id}")

def get_private_key() -> int:
    """Returns the loaded private key as an integer."""
    return _private_key

def get_public_key_hex() -> str:
    """Returns the uncompressed public key as a hex string."""
    pubkey_bytes = _public_key.x.to_bytes(32, 'big') + _public_key.y.to_bytes(32, 'big')
    return pubkey_bytes.hex()

def get_node_id() -> str:
    """Returns the node's unique ID."""
    return _node_id

def sign_message(message: bytes) -> str:
    """Signs a message (bytes) and returns the signature as a hex string."""
    if not _private_key:
        raise Exception("Identity not initialized. Cannot sign message.")
    
    # fastecdsa.ecdsa.sign returns a tuple (r, s)
    r, s = ecdsa.sign(message, _private_key, curve=CURVE, hashfunc=hashlib.sha256)
    
    # We'll concatenate r and s to form the signature
    return r.to_bytes(32, 'big').hex() + s.to_bytes(32, 'big').hex()

def verify_signature(pubkey_hex: str, signature_hex: str, message: bytes) -> bool:
    """Verifies a signature against a message using the provided public key."""
    try:
        # Reconstruct the public key from the hex string
        pubkey_bytes = bytes.fromhex(pubkey_hex)
        x = int.from_bytes(pubkey_bytes[:32], 'big')
        y = int.from_bytes(pubkey_bytes[32:], 'big')
        pub_key = keys.Point(x, y, curve=CURVE)

        # Reconstruct the signature from the hex string
        signature_bytes = bytes.fromhex(signature_hex)
        r = int.from_bytes(signature_bytes[:32], 'big')
        s = int.from_bytes(signature_bytes[32:], 'big')

        # The verify function returns True on success, raises an exception on failure
        return ecdsa.verify((r, s), message, pub_key, curve=CURVE, hashfunc=hashlib.sha256)
    except Exception:
        # Catch any errors during parsing or verification
        return False

def get_canonical_json_bytes(data: dict) -> bytes:
    """Creates a canonical (reproducible) byte representation of a JSON object."""
    return json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')