**feat(identity): add node identity management with P256 ECDSA keys**

**Contributer**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [c55a2bf0f479ef4a00ca6d76925583bd21a82095](https://github.com/The-Sycorax/denaro/commit/c55a2bf0f479ef4a00ca6d76925583bd21a82095)

**Date**: September 8th, 2025

---

### Overview
- This commit introduces `denaro/node/identity.py` for handling node identity and cryptographic operations. Provides initialization, key persistence, signing, verification, and canonical JSON serialization using the P256 (secp256r1) curve.
    
- This module ensures each node has a reproducible and secure cryptographic identity, supporting message authenticity and integrity within the system.

---

### Functions:
- **`generate_new_key`**:  
  - Creates a new elliptic curve private/public keypair using the P256 curve.
  - Returns the private key as an integer and the associated public key as a point on the curve.

- **`save_key`**:  
  - Persists the node’s private key to disk at `node_key.priv`.  
  - Ensures the key can be reloaded for consistent node identity across sessions.

- **`load_key`**:  
  - Reads and returns the stored private key from `node_key.priv`.  
  - Returns `None` if no key exists, allowing conditional generation of a new one.

- **`initialize_identity`**:  
  - Main entry point for setting up node identity.  
  - Loads the existing private key if available or generates a new one if absent.  
  - Derives the corresponding public key and computes a unique Node ID by hashing the uncompressed public key coordinates with SHA256.

- **`get_private_key`**:  
  - Returns the currently loaded private key as an integer.  
  - Used internally for signing or by other modules requiring key access.

- **`get_public_key_hex`**:  
  - Exposes the uncompressed public key (concatenated X and Y coordinates) as a hex string.  
  - Useful for publishing the node’s public key to external peers.

- **`get_node_id`**:  
  - Returns the SHA256-based unique identifier for the node.  
  - Acts as a deterministic, reproducible identity derived from the node’s public key.

- **`sign_message`**:  
  - Signs a given message (bytes) using ECDSA with SHA256 and the node’s private key.  
  - Returns the signature as a concatenated hex string of `(r, s)` values.  
  - Raises an exception if identity has not been initialized.

- **`verify_signature`**:  
  - Verifies a provided signature against a message and a given public key.  
  - Reconstructs the public key from hex, parses the `(r, s)` components of the signature, and uses ECDSA verification.  
  - Returns `True` if valid, `False` otherwise.

- **`get_canonical_json_bytes`**:  
  - Produces a canonical, reproducible byte representation of a JSON object.  
  - Ensures consistent serialization for hashing, signing, and validation across nodes.

---

### Constants:
- **`SELECTED_CURVE`**: Chosen elliptic curve (P256 / secp256r1).  
- **`KEY_FILE_PATH`**: Filesystem path for storing the node’s private key.  
- **`_private_key`**: Internal variable holding the private key.  
- **`_public_key`**: Internal variable holding the derived public key.  
- **`_node_id`**: Internal variable holding the computed Node ID.
