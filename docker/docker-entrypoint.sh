#!/bin/bash

set -euo pipefail
IFS=$'\n\t'
umask 077

# Global, set when pinggy tunnel is started
PINGGY_SSH_PID=""


log() {
  # Usage: log LEVEL message...
  # LEVEL: INFO|WARN|ERROR (any string accepted)
  local level="${1:-INFO}"
  shift || true

  local timestamp
  timestamp="$(date -u +'%Y-%m-%dT%H:%M:%S UTC')"

  # WARN/ERROR to stderr, everything else to stdout
  if [ "${level}" = "WARN" ] || [ "${level}" = "ERROR" ]; then
    echo "${timestamp} - ${level} - docker-entrypoint.sh - $*" >&2
  else
    echo "${timestamp} - ${level} - docker-entrypoint.sh - $*"
  fi
}


require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    log ERROR "${name} is not set. Exiting..."
    exit 1
  fi
}


require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log ERROR "Required command '${cmd}' not found. Exiting..."
    exit 1
  fi
}


cleanup() {
  if [ -n "${PINGGY_SSH_PID}" ]; then
    if kill -0 "${PINGGY_SSH_PID}" >/dev/null 2>&1; then
      log INFO "Stopping Pinggy tunnel (pid ${PINGGY_SSH_PID})"
      kill "${PINGGY_SSH_PID}" >/dev/null 2>&1 || true
      wait "${PINGGY_SSH_PID}" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup EXIT


# ------------------------------
# Postgres helpers
# ------------------------------
# Run psql as the cluster superuser defined by POSTGRES_USER and POSTGRES_PASSWORD
psql_super() {
  PGPASSWORD="${POSTGRES_PASSWORD}" psql \
    -X \
    -v ON_ERROR_STOP=1 \
    -q \
    -h "${DENARO_DATABASE_HOST}" \
    -U "${POSTGRES_USER}" \
    "$@"
}


wait_for_postgres() {
  export PGPASSWORD="${POSTGRES_PASSWORD}"
  log INFO "Waiting for Postgres at ${DENARO_DATABASE_HOST}:5432"
  until pg_isready -h "${DENARO_DATABASE_HOST}" -p 5432 -U "${POSTGRES_USER}" >/dev/null 2>&1
  do
    log INFO "Postgres not ready yet"
    sleep 1
  done
  log INFO "Postgres is ready"
}


sanitize_identifier() {
  # Conservative Postgres identifier: starts with [A-Za-z_], then [A-Za-z0-9_]*
  local raw="$1"
  local sanitized

  sanitized="$(echo "${raw}" | tr '-' '_' | tr -cd 'A-Za-z0-9_')"
  if [[ ! "${sanitized}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    # Ensure it starts with a safe character
    sanitized="_${sanitized}"
    sanitized="$(echo "${sanitized}" | tr -cd 'A-Za-z0-9_')"
  fi

  if [[ ! "${sanitized}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    log ERROR "Unable to derive a safe Postgres identifier from '${raw}'. Got '${sanitized}'. Exiting..."
    exit 1
  fi

  echo "${sanitized}"
}


create_node_database() {
  local db_name="$1"

  export PGPASSWORD="${POSTGRES_PASSWORD}"

  # NOTE: We intentionally avoid psql's quoted-variable forms (:'var', :"var") because
  # some environments do not expand them, leading to server-side syntax errors.
  # Since db_name is validated by sanitize_identifier, interpolation here is safe.
  local exists
  exists="$(psql_super -d "postgres" -tAc "SELECT 1 FROM pg_database WHERE datname = '${db_name}'")"

  if [ "${exists}" != "1" ]; then
    log INFO "Database '${db_name}' does not exist. Creating..."
    psql_super -d "postgres" -c "CREATE DATABASE \"${db_name}\"" >/dev/null

    log INFO "Importing database schema from denaro/schema.sql"
    psql_super -d "${db_name}" --single-transaction -f "denaro/schema.sql" >/dev/null
  else
    log INFO "Database '${db_name}' already exists."
  fi
}


# ------------------------------
# Registry helpers
# ------------------------------
with_registry_lock() {
  local lock_path="$1"
  shift

  if command -v flock >/dev/null 2>&1; then
    # Use a dedicated fd so nested locks are possible if needed
    exec 200>"${lock_path}"
    flock 200
    "$@"
    flock -u 200
    exec 200>&-
  else
    log WARN "flock not available. Proceeding without registry lock"
    "$@"
  fi
}


registry_line_count() {
  local file_path="$1"
  if [ -s "${file_path}" ]; then
    jq -e 'length' "${file_path}" 2>/dev/null || echo 0
  else
    echo 0
  fi
}


registry_append_unique() {
  local registry_file="$1"
  local url="$2"
  local node_name="$3"
  local lock_file="${registry_file}.lock"

  with_registry_lock "${lock_file}" _registry_append_unique_locked "${registry_file}" "${url}" "${node_name}"
}


_registry_append_unique_locked() {
  local registry_file="$1"
  local url="$2"
  local node_name="$3"

  if [ ! -s "${registry_file}" ]; then
    echo "{}" > "${registry_file}"
  fi

  local current_url=""
  current_url="$(jq -r --arg node "${node_name}" '.[$node] // empty' "${registry_file}" 2>/dev/null)"

  if [ "${current_url}" = "${url}" ]; then
    log INFO "Registry already contains URL for ${node_name}, skipping append"
  else
    local tmp_file
    tmp_file="$(mktemp "${registry_file}.tmp.XXXXXX")"
    if jq --arg node "${node_name}" --arg url "${url}" '.[$node] = $url' "${registry_file}" > "${tmp_file}"; then
      mv "${tmp_file}" "${registry_file}"
      log INFO "Published public URL for ${node_name} to registry"
    else
      rm -f "${tmp_file}"
      log ERROR "Failed to update registry JSON for ${node_name}"
    fi
  fi
}


registry_pick_other_peer() {
  local registry_file="$1"
  local self_url="$2"
  local lock_file="${registry_file}.lock"

  with_registry_lock "${lock_file}" _registry_pick_other_peer_locked "${registry_file}" "${self_url}"
}


_registry_pick_other_peer_locked() {
  local registry_file="$1"
  local self_url="$2"

  if [ -s "${registry_file}" ]; then
    # Pick the first value that is not self_url
    jq -r --arg self "${self_url}" 'to_entries | map(select(.value != $self)) | .[0].value // empty' "${registry_file}" 2>/dev/null || true
  fi
}


# ------------------------------
# Node: Pinggy + Discovery (refactored)
# ------------------------------
setup_pinggy_tunnel() {
  local registry_file="$1"

  if [ "${ENABLE_PINGGY_TUNNEL:-false}" != "true" ]; then
    log INFO "Pinggy tunnel not enabled. Using current self URL"
    return 0
  fi

  require_cmd ssh

  log INFO "Pinggy tunnel enabled. Starting tunnel"
  : > /tmp/pinggy.log

  # Background SSH tunnel
  ssh -n \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -p 443 \
    -R0:localhost:"${DENARO_NODE_PORT}" \
    free.pinggy.io > /tmp/pinggy.log 2>&1 &

  PINGGY_SSH_PID="$!"
  log INFO "Pinggy ssh pid ${PINGGY_SSH_PID}"

  log INFO "Waiting for Pinggy to provide a public URL"
  local counter=0
  local public_address=""

  # Up to 30 seconds for capture, scanning the log each second
  while [ "${counter}" -lt 30 ]; do
    # If tunnel process died, stop waiting early
    if ! kill -0 "${PINGGY_SSH_PID}" >/dev/null 2>&1; then
      log WARN "Pinggy ssh process exited before providing a URL"
      break
    fi

    # Match the first https endpoint for pinggy free links
    public_address="$(grep -Eo 'https://[A-Za-z0-9-]+\.a\.free\.pinggy\.link' /tmp/pinggy.log | head -n 1 || true)"
    if [ -n "${public_address}" ]; then
      log INFO "Captured public URL: ${public_address}"
      export DENARO_SELF_URL="${public_address}"
      registry_append_unique "${registry_file}" "${public_address}" "${NODE_NAME}"
      return 0
    fi

    sleep 1
    counter=$((counter + 1))
  done

  log WARN "Could not get public URL from Pinggy output. Falling back to internal URL"
  export DENARO_SELF_URL="http://${NODE_NAME}:${DENARO_NODE_PORT}"
  export ENABLE_PINGGY_TUNNEL="false"

  log WARN "Pinggy log tail for diagnostics"
  tail -n 80 /tmp/pinggy.log || true
}


discover_bootstrap_node() {
  local registry_file="$1"

  if [ "${DENARO_BOOTSTRAP_NODE:-}" != "random" ]; then
    return 0
  fi

  local topology_file="/shared/denaro-node-topology/topology.json"
  if [ ! -s "${topology_file}" ]; then
    log WARN "Topology file missing, cannot determine public nodes. Falling back to self."
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
    return 0
  fi

  local total_public
  total_public="$(jq -r '.public_nodes | length' "${topology_file}" 2>/dev/null || echo 0)"

  if [ "${total_public}" -le 0 ]; then
    log WARN "No public nodes defined in topology. Falling back to self."
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
    return 0
  fi

  local expected_others
  expected_others="$(jq -r --arg self "${NODE_NAME}" '[.public_nodes[] | select(. != $self)] | length' "${topology_file}" 2>/dev/null || echo 0)"

  if [ "${expected_others}" -eq 0 ]; then
    log WARN "No other public nodes exist in topology. Falling back to self."
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
    return 0
  fi

  log INFO "Random node requested. Waiting for all other public nodes to register..."

  local counter=0
  local max_wait_iterations=60

  while [ "${counter}" -lt "${max_wait_iterations}" ]; do
    local missing_nodes="error"
    if [ -s "${registry_file}" ]; then
      missing_nodes="$(jq -r --arg self "${NODE_NAME}" --slurpfile reg "${registry_file}" '.public_nodes | map(select(. != $self)) | map(select(in($reg[0]) | not)) | join(", ")' "${topology_file}" 2>/dev/null || echo "error")"
    fi

    if [ "${missing_nodes}" = "" ]; then
      break
    fi

    log INFO "Waiting 60 seconds... missing public nodes: ${missing_nodes}"
    sleep 2
    counter=$((counter + 1))
  done

  if [ "${counter}" -ge "${max_wait_iterations}" ]; then
    log WARN "Timed out waiting for all public nodes to register"
  fi

  local random_address=""
  if [ -s "${registry_file}" ]; then
    random_address="$(jq -r --arg self "${NODE_NAME}" --slurpfile top "${topology_file}" '
      to_entries | map(select(.key != $self and (.key | in($top[0].public_nodes | map({(.): true}) | add)))) | map(.value) | .[]
    ' "${registry_file}" 2>/dev/null | shuf -n 1 || true)"
  fi

  if [ -n "${random_address}" ]; then
    export DENARO_BOOTSTRAP_NODE="${random_address}"
    log INFO "Selected bootstrap node: ${DENARO_BOOTSTRAP_NODE}"
  else
    log WARN "No different bootstrap node found. Falling back to self"
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
  fi
}


resolve_bootstrap_node_from_name() {
  local registry_file="$1"
  local topology_file="/shared/denaro-node-topology/topology.json"

  if [ -z "${DENARO_BOOTSTRAP_NODE:-}" ]; then
    return 0
  fi

  if [[ "${DENARO_BOOTSTRAP_NODE}" == http* ]] || [ "${DENARO_BOOTSTRAP_NODE}" = "self" ] || [ "${DENARO_BOOTSTRAP_NODE}" = "random" ]; then
    return 0
  fi

  if [ ! -s "${topology_file}" ]; then
    log WARN "Topology file missing. Cannot resolve node name '${DENARO_BOOTSTRAP_NODE}'. Falling back to self."
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
    return 0
  fi

  local is_valid="false"
  if jq -e --arg node "${DENARO_BOOTSTRAP_NODE}" '.public_nodes[] | select(. == $node)' "${topology_file}" >/dev/null 2>&1; then
    is_valid="true"
  fi

  if [ "${is_valid}" = "true" ]; then
    log INFO "Bootstrap node '${DENARO_BOOTSTRAP_NODE}' matched a node name in topology. Waiting for its registry URL..."
    local counter=0
    local max_wait_iterations=60
    local discovered_url=""

    while [ "${counter}" -lt "${max_wait_iterations}" ]; do
      if [ -s "${registry_file}" ]; then
        discovered_url="$(jq -r --arg node "${DENARO_BOOTSTRAP_NODE}" '.[$node] // empty' "${registry_file}" 2>/dev/null)"
      fi
      
      if [ -n "${discovered_url}" ]; then
        break
      fi
      log INFO "Waiting 60 seconds for node '${DENARO_BOOTSTRAP_NODE}'... (attempt $((counter + 1)))"
      sleep 2
      counter=$((counter + 1))
    done

    if [ -n "${discovered_url}" ]; then
      log INFO "Resolved bootstrap node '${DENARO_BOOTSTRAP_NODE}' -> '${discovered_url}'"
      export DENARO_BOOTSTRAP_NODE="${discovered_url}"
    else
      log WARN "Timeout: Node '${DENARO_BOOTSTRAP_NODE}' did not publish its URL to the registry. Falling back to self."
      export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
    fi
  else
    log WARN "Bootstrap node '${DENARO_BOOTSTRAP_NODE}' is not a public node in topology.json. Falling back to self."
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
  fi
}


# ------------------------------
# dotenv writer (quoted values)
# ------------------------------
dotenv_quote() {
  # Returns a double-quoted, escaped value safe for sourcing and for most .env parsers.
  # Escapes: \ " $ ` and normalizes CRLF/newlines/tabs.
  local v="$1"
  v="${v//$'\r'/}"
  v="${v//$'\n'/\\n}"
  v="${v//$'\t'/\\t}"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//\$/\\\$}"
  v="${v//\`/\\\`}"
  printf "\"%s\"" "${v}"
}


dotenv_kv() {
  # Usage: dotenv_kv KEY VALUE
  local k="$1"
  local v="$2"
  printf '%s=%s\n' "${k}" "$(dotenv_quote "${v}")"
}


print_env_file() {
  local path="$1"

  echo
  echo "Generated .env file:"
  echo "################################################################################"

    # Redact sensitive values.
    # Keep quoting style consistent with the file.
    local sed_expr=""
    sed_expr+='s/^POSTGRES_USER=.*/POSTGRES_USER="***REDACTED***"/;'
    sed_expr+='s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD="***REDACTED***"/;'
    sed -E "${sed_expr}" "${path}"

  echo "################################################################################"
  echo
}


write_env_file() {
  local path="$1"
  {
    dotenv_kv DENARO_NODE_HOST "${DENARO_NODE_HOST}"
    dotenv_kv DENARO_NODE_PORT "${DENARO_NODE_PORT}"
    dotenv_kv DENARO_SELF_URL "${DENARO_SELF_URL}"
    dotenv_kv DENARO_BOOTSTRAP_NODE "${DENARO_BOOTSTRAP_NODE}"
    echo
    dotenv_kv DENARO_DATABASE_NAME "${DB_NAME}"
    dotenv_kv DENARO_DATABASE_HOST "${DENARO_DATABASE_HOST}"
    dotenv_kv POSTGRES_USER "${POSTGRES_USER}"
    dotenv_kv POSTGRES_PASSWORD "${POSTGRES_PASSWORD}"
    echo
    dotenv_kv LOG_LEVEL "${LOG_LEVEL}"
    dotenv_kv LOG_FORMAT "${LOG_FORMAT}"
    dotenv_kv LOG_DATE_FORMAT "${LOG_DATE_FORMAT}"
    dotenv_kv LOG_CONSOLE_HIGHLIGHTING "${LOG_CONSOLE_HIGHLIGHTING}"
    dotenv_kv LOG_INCLUDE_REQUEST_CONTENT "${LOG_INCLUDE_REQUEST_CONTENT}"
    dotenv_kv LOG_INCLUDE_RESPONSE_CONTENT "${LOG_INCLUDE_RESPONSE_CONTENT}"
    dotenv_kv LOG_INCLUDE_BLOCK_SYNC_MESSAGES "${LOG_INCLUDE_BLOCK_SYNC_MESSAGES}"
  } > "${path}"

  chmod 600 "${path}" || true
  print_env_file "${path}"
}


start_denaro_node() {
  local pid=""

  log INFO "Starting Denaro Node via 'python /app/run_node.py'"; echo
  python /app/run_node.py &
  pid="$!"

  forward_term() {
    if kill -0 "${pid}" >/dev/null 2>&1; then
      log INFO "Forwarding termination signal to Denaro node (pid ${pid})"
      kill -TERM "${pid}" >/dev/null 2>&1 || true
    fi
  }

  trap forward_term SIGTERM SIGINT

  wait "${pid}"
}


# ------------------------------
# Node mode
# ------------------------------
node_main() {
  echo
  echo "################################################################################"
  echo "--- Denaro Node Entrypoint for ${NODE_NAME:-<unset>} ---"
  echo "################################################################################"
  echo

  require_cmd psql
  require_cmd pg_isready

  require_env POSTGRES_USER
  require_env POSTGRES_PASSWORD
  require_env DENARO_DATABASE_HOST

  require_env DENARO_NODE_HOST
  require_env NODE_NAME
  require_env DENARO_NODE_PORT

  local registry_dir="/shared/denaro-node-registry"
  local registry_file="${registry_dir}/public_nodes.json"
  mkdir -p "${registry_dir}"
  if [ ! -s "${registry_file}" ]; then
    echo "{}" > "${registry_file}"
  fi

  # DB name
  if [ -n "${DENARO_DATABASE_NAME:-}" ]; then
    DB_NAME="$(sanitize_identifier "${DENARO_DATABASE_NAME}")"
  else
    DB_NAME="$(sanitize_identifier "${NODE_NAME}")"
  fi
  export DB_NAME

  # Self URL default
  if [ -z "${DENARO_SELF_URL:-}" ]; then
    export DENARO_SELF_URL="http://${NODE_NAME}:${DENARO_NODE_PORT}"
  fi

  # Bootstrap default
  if [ -z "${DENARO_BOOTSTRAP_NODE:-}" ]; then
    export DENARO_BOOTSTRAP_NODE="https://node.denaro.network"
  fi

  # Logging defaults
  export LOG_LEVEL="${LOG_LEVEL:-INFO}"
  export LOG_FORMAT="${LOG_FORMAT:-%(asctime)s - %(levelname)s - %(name)s - %(message)s}"
  export LOG_DATE_FORMAT="${LOG_DATE_FORMAT:-%Y-%m-%dT%H:%M:%S}"
  export LOG_INCLUDE_REQUEST_CONTENT="${LOG_INCLUDE_REQUEST_CONTENT:-False}"
  export LOG_INCLUDE_RESPONSE_CONTENT="${LOG_INCLUDE_RESPONSE_CONTENT:-False}"
  export LOG_INCLUDE_BLOCK_SYNC_MESSAGES="${LOG_INCLUDE_BLOCK_SYNC_MESSAGES:-False}"
  export LOG_CONSOLE_HIGHLIGHTING="${LOG_CONSOLE_HIGHLIGHTING:-True}"

  # --- Stage 1: Pinggy (optional) ---
  setup_pinggy_tunnel "${registry_file}"

  resolve_bootstrap_node_from_name "${registry_file}"

  # --- Stage 2/3: Discovery (optional) ---
  discover_bootstrap_node "${registry_file}"

  # "self" literal resolution
  if [ "${DENARO_BOOTSTRAP_NODE}" = "self" ]; then
    export DENARO_BOOTSTRAP_NODE="${DENARO_SELF_URL}"
  fi

  # --- Stage 4: env + db + launch ---
  log INFO "Writing /app/.env"
  write_env_file "/app/.env"

  wait_for_postgres

  log INFO "Setting up database: ${DB_NAME}"
  create_node_database "${DB_NAME}"
  log INFO "Database ready"

  unset PGPASSWORD

  log INFO "Entrypoint script finished"
  start_denaro_node
}


# ------------------------------
# pgAdmin mode
# ------------------------------
pgadmin_main() {
  if [ "${DOCKER_ENABLE_PGADMIN:-false}" != "true" ]; then
    log INFO "pgAdmin is disabled by configuration (DOCKER_ENABLE_PGADMIN). Exiting..."
    exit 0
  fi

  log INFO "--- pgadmin starting ---"

  require_env DENARO_DATABASE_HOST
  require_env POSTGRES_USER
  require_env POSTGRES_PASSWORD

  local pgadmin_config_dir="/var/lib/pgadmin"
  local pgpass_file="${pgadmin_config_dir}/pgpass"

  log INFO "Writing servers.json to ${pgadmin_config_dir}/servers.json"
  mkdir -p "${pgadmin_config_dir}"

  cat << EOF > "${pgadmin_config_dir}/servers.json"
{
  "Servers": {
    "1": {
      "Name": "Denaro Postgres Databases",
      "Group": "Servers",
      "Host": "${DENARO_DATABASE_HOST}",
      "Port": 5432,
      "MaintenanceDB": "postgres",
      "Username": "${POSTGRES_USER}",
      "PassFile": "${pgpass_file}",
      "UseSSHTunnel": 0,
      "TunnelPort": "22",
      "TunnelAuthentication": 0,
      "KerberosAuthentication": false,
      "SSLMode": "prefer"
    }
  }
}
EOF

  log INFO "Writing pgpass file"
  echo "*:*:*:${POSTGRES_USER}:${POSTGRES_PASSWORD}" > "${pgpass_file}"
  chown -R 5050:5050 "${pgadmin_config_dir}" || true
  chmod 700 "${pgadmin_config_dir}" || true
  chmod 600 "${pgadmin_config_dir}/servers.json" || true
  chmod 600 "${pgpass_file}" || true

  log INFO "Executing official pgadmin4 entrypoint"
  exec /entrypoint.sh "$@"
}


# ------------------------------
# Entry point
# ------------------------------
MODE="${1:-node}"

case "${MODE}" in
  pgadmin)
    shift || true
    pgadmin_main "$@"
    ;;
  node)
    node_main
    ;;
  *)
    exec "$@"
    ;;
esac