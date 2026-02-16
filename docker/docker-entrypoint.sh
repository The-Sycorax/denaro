#!/bin/bash

###############################################################################
# Denaro Node Container Entrypoint
#
# Overview
#   This script launches a Denaro node inside a container. It prepares runtime
#   configuration, optionally acquires a public URL via Pinggy, coordinates
#   bootstrap peer discovery through a registry file, ensures the node-specific
#   PostgreSQL database exists and has the required schema, then starts the
#   Denaro node process.
#
# Lifecycle and responsibilities
#   1. Initialization:
#        - The shared peer registry directory is created
#        - A deterministic database name and internal self URL is derived.
#        - Bootstrap intent is normalized when unset.
#
#   2. Public URL acquisition and publication:
#        - If tunneling is enabled, a reverse tunnel is extablished via `ssh` 
#          using Pinggy's free tunnleing service (free.pinggy.io).
#        - The tunnel output is parsed to capture the public HTTPS URL.
#        - The captured URL is written to the shared registry file for peer discovery.
#        - If capture fails, the internal URL is retained and tunneling is disabled.
#
#   3. Peer discovery:
#        - Public bootstrap nodes wait for the appearance of another public peer.
#        - Private nodes in discovery mode wait for any public node.
#        - A peer URL that is not the current node is selected when available.
#        - If a timeout occurs or no peer is available, the bootstrap target is
#          set to `DENARO_SELF_URL` .
#
#   4. Env generation, database provisioning, and application launch:
#        - An application `.env` file is generated with the required variables.
#        - PostgreSQL readiness is awaited.
#        - The node database is created if it does not already exist.
#        - The schema is imported only on first database creation.
#        - Finally, the Denaro node is started via `python run_node.py``.
#
# Inputs via environment:
#   NODE_NAME                   Name of this node
#   DENARO_NODE_HOST            Hostname or IP the node binds to or advertises
#   DENARO_NODE_PORT            Port that the node listens on inside the container
#   ENABLE_PINGGY_TUNNEL        When "true", Pinggy tunneling is enabled to expose
#                               the node on the Internet.
#   DENARO_BOOTSTRAP_NODE       "self", "discover", or any explicit URL
#   DENARO_DATABASE_HOST        Hostname of the database service
#   POSTGRES_USER               Database user
#   POSTGRES_PASSWORD           Database password
#
# Outputs and artifacts:
#   - A registry file at /shared/registry/public_nodes.txt contains public URLs when available.
#   - A .env file is written in the working directory for the application runtime.
#   - A PostgreSQL database named "denaro_<sanitized node name>" is created if absent.
#
# Fallback strategy and exit behavior:
#   - The script is designed to be resilient and avoid exiting on recoverable issues.
#   - Tunnel failure does not cause exit. The internal URL is used and tunneling is disabled.
#   - Discovery timeout does not cause exit. The bootstrap target becomes self.
#   - Absence of a different public URL does not cause exit. The bootstrap target becomes self.
#   - Database or unrecoverable runtime errors may still cause exit due to `set -e`.
#
###############################################################################

set -e

echo "--- Denaro Node Container Entrypoint for ${NODE_NAME} ---"

# --- CONFIGURATION ---
REGISTRY_DIR="/shared/node-registry"                          # Shared registry directory
REGISTRY_FILE="${REGISTRY_DIR}/public_nodes.txt"              # Shared peer registry file
mkdir -p "$REGISTRY_DIR"                                      # Ensure registry directory exists

SANITIZED_NODE_NAME=$(echo "${NODE_NAME}" | tr '-' '_')       # Replace hyphens with underscores for DB naming
DB_NAME="denaro_${SANITIZED_NODE_NAME}"                       # Per-node database name

if [ -n "${DENARO_DATABASE_NAME}" ]; then
  DB_NAME="${DENARO_DATABASE_NAME}"
fi

# Default to internal URL if unset
if [ -z "${DENARO_SELF_URL}" ]; then
  export DENARO_SELF_URL="http://${NODE_NAME}:${DENARO_NODE_PORT}"
fi

# Normalize bootstrap intent default if unset
if [ -z "${DENARO_BOOTSTRAP_NODE}" ]; then
  export DENARO_BOOTSTRAP_NODE="https://node.denaro.network"
fi

if [ -z "${LOG_LEVEL}" ]; then
  export LOG_LEVEL="INFO"
fi

if [ -z "${LOG_FORMAT}" ]; then
  export LOG_FORMAT="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
fi

if [ -z "${LOG_DATE_FORMAT}" ]; then
  export LOG_DATE_FORMAT="%Y-%m-%dT%H:%M:%S"
fi

if [ -z "${LOG_INCLUDE_REQUEST_CONTENT}" ]; then
  export LOG_INCLUDE_REQUEST_CONTENT="False"
fi

if [ -z "${LOG_INCLUDE_RESPONSE_CONTENT}" ]; then
  export LOG_INCLUDE_RESPONSE_CONTENT="False"
fi

if [ -z "${LOG_INCLUDE_BLOCK_SYNC_MESSAGES}" ]; then
  export LOG_INCLUDE_BLOCK_SYNC_MESSAGES="False"
fi

if [ -z "${LOG_CONSOLE_HIGHLIGHTING}" ]; then
  export LOG_CONSOLE_HIGHLIGHTING="True"
fi


# --- STAGE 1: OPTIONAL PUBLIC TUNNEL VIA PINGGY ---
# If tunneling is requested, attempt to capture a public URL. Failure falls back to internal URL.
if [ "${ENABLE_PINGGY_TUNNEL}" = "true" ]; then
  echo "Pinggy tunnel enabled. Starting tunnel..."
  # Launch the tunnel in the background. Suppress strict host checks due to ephemeral endpoints.
  ssh -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -p 443 -R0:localhost:${DENARO_NODE_PORT} free.pinggy.io > /tmp/pinggy.log 2>&1 &

  echo "Waiting for Pinggy to provide a public URL..."
  COUNTER=0
  PUBLIC_ADDRESS=""
  # Up to 30 seconds for capture, scanning the log each second
  while [ $COUNTER -lt 30 ]; do
    # Extract the first matching https endpoint from Pinggy output
    PUBLIC_ADDRESS=$(grep -o 'https://[a-zA-Z0-9-]*\.a\.free\.pinggy\.link' /tmp/pinggy.log | head -n 1 || true)
    if [ -n "$PUBLIC_ADDRESS" ]; then
      echo "SUCCESS: Captured public URL: ${PUBLIC_ADDRESS}"
      export DENARO_SELF_URL="${PUBLIC_ADDRESS}"                 # Promote captured public URL to self URL
      echo "${PUBLIC_ADDRESS}" >> "${REGISTRY_FILE}"             # Publish to registry for peer discovery
      echo "Published public URL to registry."
      break
    fi
    sleep 1
    COUNTER=$((COUNTER+1))
  done

  # Fallback if no public URL could be captured
  if [ -z "$PUBLIC_ADDRESS" ]; then
    echo "WARNING: Could not get public URL from Pinggy output. Falling back to internal URL."
    echo "Disabling tunneling to prevent dependent logic from waiting on a public endpoint."
    export DENARO_SELF_URL="http://${NODE_NAME}:${DENARO_NODE_PORT}"
    export ENABLE_PINGGY_TUNNEL="false"
    # Show last lines of the log for diagnostics without aborting
    echo "Pinggy log tail for diagnostics:"
    tail -n 50 /tmp/pinggy.log || true
  fi
else
  echo "Pinggy tunnel not enabled. Using internal URL for self."
fi

# --- STAGE 2 AND 3: BOOTSTRAP DISCOVERY WHEN REQUESTED ---
# Discovery runs only when DENARO_BOOTSTRAP_NODE equals "discover".
# If a fixed bootstrap address is provided, this entire block is skipped.
if [ "${DENARO_BOOTSTRAP_NODE}" = "discover" ]; then
  # Determine how many URLs to expect in the registry
  # Public nodes that are discoverable wait for a partner. Private nodes wait for any public node.
  EXPECTED_URLS=1
  if [ "${ENABLE_PINGGY_TUNNEL}" = "true" ]; then
    EXPECTED_URLS=2
    echo "Discovery requested and tunneling enabled. Waiting for a second public node to register..."
  else
    echo "Discovery requested. Waiting for any public node to register..."
  fi

  COUNTER=0
  MAX_WAIT_ITERATIONS=60  # 60 iterations at 2 seconds each is about 120 seconds

  # Wait for the registry file to reach the expected count
  while [ "$(wc -l < "${REGISTRY_FILE}" 2>/dev/null || echo 0)" -lt "${EXPECTED_URLS}" ] && [ $COUNTER -lt $MAX_WAIT_ITERATIONS ]; do
    CURRENT=$(wc -l < "${REGISTRY_FILE}" 2>/dev/null || echo 0)
    echo "Waiting for public nodes... found ${CURRENT}/${EXPECTED_URLS}"
    sleep 2
    COUNTER=$((COUNTER+1))
  done

  # If registry is still short, apply fallback instead of exiting
  if [ "$(wc -l < "${REGISTRY_FILE}" 2>/dev/null || echo 0)" -lt "${EXPECTED_URLS}" ]; then
    echo "WARNING: Timed out waiting for enough public nodes to register."
    # Fallback policy keeps the node operable and able to gossip later
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
    echo "Applying fallback for discovery: setting DENARO_BOOTSTRAP_NODE to self."
  else
    # Registry has enough lines. Pick a peer that is not ourselves
    OTHER_PUBLIC_ADDRESS=$(grep -v "${DENARO_SELF_URL}" "${REGISTRY_FILE}" | head -n 1 || true)
    if [ -n "${OTHER_PUBLIC_ADDRESS}" ]; then
      export DENARO_BOOTSTRAP_NODE="${OTHER_PUBLIC_ADDRESS}"
      echo "Discovered and selected bootstrap peer: ${DENARO_BOOTSTRAP_NODE}"
    else
      echo "WARNING: Could not discover a different bootstrap peer URL. Falling back to self."
      export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
    fi
  fi
fi

# Final resolution when the literal value is "self"
if [ "${DENARO_BOOTSTRAP_NODE}" = "self" ]; then
  export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
fi

# --- STAGE 4: CONFIGURE AND LAUNCH ---
echo "Configuring .env file for ${NODE_NAME}..."
cat << EOF > /app/.env
DENARO_NODE_HOST=${DENARO_NODE_HOST}
DENARO_NODE_PORT=${DENARO_NODE_PORT}
DENARO_SELF_URL=${DENARO_SELF_URL}
DENARO_BOOTSTRAP_NODE=${DENARO_BOOTSTRAP_NODE}

DENARO_DATABASE_NAME=${DB_NAME}
DENARO_DATABASE_HOST=${DENARO_DATABASE_HOST}
DENARO_DATABASE_USER=${POSTGRES_USER}
DENARO_DATABASE_PASSWORD=${POSTGRES_PASSWORD}

POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

LOG_LEVEL=${LOG_LEVEL}
LOG_FORMAT=${LOG_FORMAT}
LOG_DATE_FORMAT=${LOG_DATE_FORMAT}
LOG_CONSOLE_HIGHLIGHTING=${LOG_CONSOLE_HIGHLIGHTING}
LOG_INCLUDE_REQUEST_CONTENT=${LOG_INCLUDE_REQUEST_CONTENT}
LOG_INCLUDE_RESPONSE_CONTENT=${LOG_INCLUDE_RESPONSE_CONTENT}
LOG_INCLUDE_BLOCK_SYNC_MESSAGES=${LOG_INCLUDE_BLOCK_SYNC_MESSAGES}
EOF

echo "Generated .env file:"
cat /app/.env
echo "----------------------------------------"

# Database provisioning
echo "Setting up database: ${DB_NAME}"
export PGPASSWORD="${POSTGRES_PASSWORD}"

# Wait until Postgres reports readiness
until pg_isready -h "${DENARO_DATABASE_HOST}" -U "${POSTGRES_USER}" > /dev/null 2>&1; do
  echo "Postgres is unavailable - sleeping"
  sleep 1
done
echo "PostgreSQL is ready."

# Create database if missing and import schema only on first creation
if ! psql -h "${DENARO_DATABASE_HOST}" -U "${POSTGRES_USER}" -d "postgres" -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1; then
  echo "Database '${DB_NAME}' does not exist. Creating..."
  psql -h "${DENARO_DATABASE_HOST}" -U "${POSTGRES_USER}" -d "postgres" -c "CREATE DATABASE \"${DB_NAME}\""
  echo "Importing database schema from schema.sql..."
  psql -h "${DENARO_DATABASE_HOST}" -U "${POSTGRES_USER}" -d "${DB_NAME}" < denaro/schema.sql
else
  echo "Database '${DB_NAME}' already exists."
fi

unset PGPASSWORD

# Launch the Denaro Node
echo "Starting Denaro node on 0.0.0.0:${DENARO_NODE_PORT}..."
exec python /app/run_node.py