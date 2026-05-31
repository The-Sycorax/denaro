#!/bin/bash

# Author: The-Sycorax (https://github.com/The-Sycorax)
# License: MIT
# Copyright (c) 2024-2026
#
# Overview:
# This bash script automates the setup required to run a Denaro node. It
# detects the system's package manager (or accepts an explicit override),
# installs the required system packages, configures environment variables in
# a .env file, provisions and configures the PostgreSQL database, sets up a
# Python virtual environment, installs Python dependencies, and starts the
# Denaro node.
#
# Supported package managers:
#   - apt    (Debian, Ubuntu, and derivatives)
#   - dnf    (Fedora, RHEL 8+, Rocky Linux, AlmaLinux)
#   - pacman (Arch Linux, Manjaro)
#   - zypper (openSUSE, SUSE Linux Enterprise)
#
# CLI arguments:
#   --skip-prompts            Run non-interactively, using default values.
#   --setup-db                Configure system packages and PostgreSQL only.
#   --skip-package-install    Skip system package installation.
#   --package-manager <pm>    Override auto-detection. Accepts one of:
#                             apt, dnf, pacman, zypper.
#
# Notes on robustness:
#   - All .env reads/writes go through dedicated helpers (write_env_var,
#     load_env_variables, read_env_variable, dequote_env_value) that handle
#     special characters in values (slashes, ampersands, single quotes,
#     spaces) safely. Values are stored single-quoted with the standard
#     POSIX `'\''` escape for embedded single quotes.
#   - All SQL identifiers (database, role) are validated against a strict
#     [A-Za-z_][A-Za-z0-9_]* pattern before being interpolated into psql
#     commands. The role password is passed via psql variable substitution
#     (-v + :'name') so the SQL parser, not the shell, performs quoting.
#   - PostgreSQL service operations and pg_hba.conf editing are idempotent
#     and recognise both `md5` and `scram-sha-256` as already-good
#     authentication methods.


# =============================================================================
# Runtime requirement: Bash 4+
# =============================================================================
# The script uses bash-4 features pervasively: case-folding parameter
# expansion (${var,,}), associative substring matching with [[ == ]] inside
# quoted joins, `printf -v` with arbitrary names, and `readarray`. Some
# distributions still ship an older /bin/sh-style shell; refusing to run
# under bash 3 is far better than failing later with a confusing
# "bad substitution" or syntax error from a feature the operator did not
# realise was being used.
if [ -z "${BASH_VERSINFO+x}" ] || (( BASH_VERSINFO[0] < 4 )); then
    echo "This script requires Bash 4 or newer." >&2
    echo "Detected shell: ${BASH_VERSION:-unknown}" >&2
    echo "Re-run the script with a modern bash, e.g. 'bash ./setup.sh'." >&2
    exit 1
fi


# =============================================================================
# CLI argument parsing
# =============================================================================
# Flags are parsed in order. Unknown flags trigger an immediate exit so a typo
# never silently invokes a default behavior. --package-manager takes a value
# and validates it against the supported set; whether the corresponding binary
# is actually installed on the host is verified later by detect_package_manager.
SKIP_PACKAGE_INSTALL=false
SKIP_PROMPTS=false
SETUP_DB_ONLY=false

# PKG_MGR is left empty here so detect_package_manager will probe the system.
# If the user passes --package-manager <pm>, this is set early and detection
# becomes a validation pass instead.
PKG_MGR=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --skip-prompts) SKIP_PROMPTS=true ;;
        --setup-db) SETUP_DB_ONLY=true ;;
        --skip-package-install) SKIP_PACKAGE_INSTALL=true ;;
        --package-manager)
            # Override auto-detection. Useful when multiple supported package
            # managers are present (e.g. apt + snap on Ubuntu, dnf + flatpak
            # on Fedora) or when running in containers where the default
            # detection order may select an undesired manager.
            shift
            case "$1" in
                apt|dnf|pacman|zypper) PKG_MGR="$1" ;;
                "") echo "--package-manager requires a value. Must be one of: apt, dnf, pacman, zypper"; exit 1 ;;
                *) echo "Invalid package manager: '$1'. Must be one of: apt, dnf, pacman, zypper"; exit 1 ;;
            esac
            ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

echo "Starting Denaro node setup..."
echo ""

# =============================================================================
# Default configuration values
# =============================================================================
# These values populate the .env file when no existing value is present and
# the user accepts defaults (interactively or via --skip-prompts). They mirror
# the schema described in the README's "Environment Configuration" section.
# When an existing .env is being updated, the prompt for each variable shows
# the current value; leaving the input blank preserves the current value.
POSTGRES_USER="denaro"
POSTGRES_PASSWORD="denaro"
DENARO_DATABASE_NAME="denaro"
DENARO_DATABASE_HOST="127.0.0.1"
DENARO_NODE_HOST="127.0.0.1"
DENARO_NODE_PORT="3006"
DENARO_SELF_URL=""
DENARO_BOOTSTRAP_NODE="https://node.denaro.network"
LOG_LEVEL="INFO"
LOG_CONSOLE_HIGHLIGHTING="True"

# Tracks whether the user opted into the "use defaults for everything" path.
# Read by update_variable to suppress prompts even when SKIP_PROMPTS is false.
USE_DEFAULT_ENV_VARS=false

# Path to the .env file (relative to the script's cwd, which must be the
# Denaro repository root).
env_file=".env"

# Track which database identity values changed during the .env configuration
# step. Used by setup_database to decide whether to rotate the password.
# A change to POSTGRES_USER or DENARO_DATABASE_NAME is reported to the
# operator (the script provisions the new role/database but does not
# decommission the old one; that requires manual cleanup).
db_user_changed=false
db_pass_changed=false
db_name_changed=false

# Directory in which the Python virtual environment is created.
VENV_DIR="venv"

# Resolved path to a Python interpreter that meets the minimum version
# requirement.  Set by resolve_python after package installation; every
# subsequent python / pip invocation MUST use this variable instead of bare
# `python3`, `python`, `pip`, or `pip3`.
PYTHON_CMD=""

# Minimum acceptable Python version.  The project requires 3.8+.
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=8

# Save the original directory so we can restore it after operations that
# require chdir-ing elsewhere (e.g. /tmp for the sudo -u postgres calls).
original_dir=$(pwd)

# Ordered list of variables managed by this script. Used by
# load_env_variables, identify_missing_variables, and set_env_variables.
ENV_VARS=(
    POSTGRES_USER
    POSTGRES_PASSWORD
    DENARO_DATABASE_NAME
    DENARO_DATABASE_HOST
    DENARO_NODE_HOST
    DENARO_NODE_PORT
    DENARO_SELF_URL
    DENARO_BOOTSTRAP_NODE
    LOG_LEVEL
    LOG_CONSOLE_HIGHLIGHTING
)


# =============================================================================
# Generic helpers
# =============================================================================
# prompt_yes_no: read a y/n response from stdin, accepting y, yes, n, or no
# in any case. Re-prompts on invalid input. Sets the named variable to the
# canonical lowercase form ("y" or "n").
#
# Args:
#   $1 prompt   Prompt text to display.
#   $2 var_name Name of the variable that will receive the canonical answer.
prompt_yes_no() {
    local prompt="$1"
    local var_name="$2"
    local answer canonical
    while true; do
        read -p "$prompt " answer
        canonical="${answer,,}"
        case "$canonical" in
            y|yes)
                printf -v "$var_name" '%s' "y"
                return 0
                ;;
            n|no)
                printf -v "$var_name" '%s' "n"
                return 0
                ;;
            *)
                echo "Invalid input. Please enter 'y' or 'n'."
                echo ""
                ;;
        esac
    done
}


# validate_sql_identifier: return success if the argument is a safe SQL
# identifier (letter or underscore followed by letters, digits, underscores).
# Used to gate all interpolation of POSTGRES_USER and DENARO_DATABASE_NAME
# into psql commands.
validate_sql_identifier() {
    local name="$1"
    [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]
}


# is_local_db_host: return success (0) when the argument names the local
# machine, failure (1) otherwise.
#
# Used by setup_database to decide whether to provision a local PostgreSQL
# cluster. A blank value is treated as "local" because the runtime config
# uses Unix-socket connections in that case, which only succeed against a
# local cluster anyway. The literal hostname `localhost` is also accepted
# because PostgreSQL clients resolve it to a loopback address before
# choosing a transport (TCP or, on some systems, a Unix socket).
#
# Anything else is treated as remote. This is intentionally conservative:
# even a non-loopback IPv4 address that happens to belong to a local
# interface counts as remote here, because the script's pg_hba.conf
# rewrites only target the loopback rules, and provisioning a local cluster
# would not help an operator who really did mean to point at a different
# machine.
is_local_db_host() {
    local h="$1"
    case "$h" in
        ""|127.0.0.1|::1|localhost) return 0 ;;
        *) return 1 ;;
    esac
}


# =============================================================================
# .env file helpers
# =============================================================================
# write_env_var: persist `var_name='value'` into the target file, creating
# or replacing the existing line. Single quotes inside `value` are escaped
# using the standard POSIX shell idiom `'\''`. The function is safe for
# values containing slashes, ampersands, backslashes, single quotes, dollar
# signs, and whitespace.
#
# Args:
#   $1 var_name Name of the variable to write.
#   $2 value    Value to persist (raw, will be quoted).
#   $3 target   Path to the target .env file.
write_env_var() {
    local var_name="$1"
    local value="$2"
    local target="$3"

    # Replace each ' with the 4-character sequence '\''. Inside the parameter
    # expansion, the search pattern \' matches a literal single quote and the
    # replacement \'\\\'\' produces the four characters: ' \ ' '
    local escaped="${value//\'/\'\\\'\'}"
    local new_line="${var_name}='${escaped}'"

    if [ ! -f "$target" ]; then
        printf '%s\n' "$new_line" > "$target"
        return 0
    fi

    local tmp
    tmp=$(mktemp) || return 1
    local found=false
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" == "${var_name}="* ]]; then
            # Replace the FIRST matching line and drop any further
            # duplicates. A hand-edited .env that contains the same
            # variable twice will collapse to a single canonical line on
            # the next write, eliminating ambiguity for downstream readers.
            if ! $found; then
                printf '%s\n' "$new_line" >> "$tmp"
                found=true
            fi
        else
            printf '%s\n' "$line" >> "$tmp"
        fi
    done < "$target"

    if ! $found; then
        printf '%s\n' "$new_line" >> "$tmp"
    fi

    mv "$tmp" "$target"
}


# dequote_env_value: inverse of the quoting performed by write_env_var.
# Strips a single layer of single quotes (if present) and converts the
# `'\''` escape back to a literal single quote. Values that are not
# wrapped in single quotes are returned verbatim, supporting hand-edited
# .env files that follow the simple `VAR=value` form.
dequote_env_value() {
    local v="$1"
    if [[ "$v" =~ ^\'(.*)\'$ ]]; then
        v="${BASH_REMATCH[1]}"
        # Replace the 4-character sequence '\'' with a literal '
        v="${v//\'\\\'\'/\'}"
    fi
    printf '%s' "$v"
}


# load_env_variables: parse the .env file line by line and set the
# corresponding shell globals (only for variables in ENV_VARS, to avoid
# polluting unrelated names). Comments and blank lines are skipped.
# Values are dequoted via dequote_env_value, which avoids the security
# pitfalls of `eval`-style sourcing.
#
# Empty values are intentionally NOT applied to the shell global (preserving
# script defaults) EXCEPT for explicitly allowed variables.
load_env_variables() {
    [ -f "$env_file" ] || return 0
    local key value line decoded
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and blank lines.
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        # Match VAR=<rest>; tolerate trailing whitespace inside <rest>.
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            local known=false
            local v
            for v in "${ENV_VARS[@]}"; do
                if [[ "$v" == "$key" ]]; then
                    known=true
                    break
                fi
            done
            if $known; then
                decoded=$(dequote_env_value "$value")
                if [ -n "$decoded" ]; then
                    printf -v "$key" '%s' "$decoded"
                elif [[ "$key" == "DENARO_SELF_URL" || "$key" == "DENARO_BOOTSTRAP_NODE" ]]; then
                    # Explicitly allow these to override defaults with an empty value
                    printf -v "$key" '%s' ""
                fi
            fi
        fi
    done < "$env_file"
}


# read_env_variable: return the dequoted value of the named variable from
# the .env file. Empty string if the variable is absent or has no value.
# Used by set_env_variables to snapshot values for change detection.
read_env_variable() {
    local var_name="$1"
    [ -f "$env_file" ] || { printf ''; return 0; }
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" =~ ^${var_name}=(.*)$ ]]; then
            dequote_env_value "${BASH_REMATCH[1]}"
            return 0
        fi
    done < "$env_file"
    printf ''
    return 0
}


# identify_missing_variables: emit a space-separated list of variables that
# are absent from the .env file or whose dequoted value is empty. The
# emptiness check correctly classifies `VAR=''` and `VAR=` as missing,
# except for explicitly allowed variables.
identify_missing_variables() {
    local target="$1"
    local missing=()
    local v current found
    for v in "${ENV_VARS[@]}"; do
        if [ ! -f "$target" ]; then
            missing+=("$v")
            continue
        fi
        current=""
        found=false
        local line
        while IFS= read -r line || [ -n "$line" ]; do
            if [[ "$line" =~ ^${v}=(.*)$ ]]; then
                current=$(dequote_env_value "${BASH_REMATCH[1]}")
                found=true
                break
            fi
        done < "$target"
        
        if ! $found; then
            missing+=("$v")
        elif [ -z "$current" ]; then
            # Allow these specific variables to be legitimately blank
            if [[ "$v" != "DENARO_SELF_URL" && "$v" != "DENARO_BOOTSTRAP_NODE" ]]; then
                missing+=("$v")
            fi
        fi
    done
    echo "${missing[@]}"
}


# =============================================================================
# Package manager support
# =============================================================================
# Detect the system's package manager, or validate the override supplied via
# --package-manager. Sets the global PKG_MGR variable.
#
# Probe order (apt-get, dnf, pacman, zypper) is chosen by descending estimated
# share among Linux distributions; this gives the most natural default on
# systems where multiple package managers happen to be present (e.g. distros
# that ship with snap or flatpak alongside the native PM).
#
# When PKG_MGR is already set on entry (most likely from --package-manager),
# the function only verifies that the corresponding binary exists on PATH and
# returns early. This produces a clear up-front error instead of a confusing
# failure later during a per-PM dispatch.
detect_package_manager() {
    if [ -n "$PKG_MGR" ]; then
        # apt is special-cased because the modern wrapper is `apt`, but the
        # historical, scriptable binary is `apt-get`. We probe `apt-get` to
        # match the auto-detection branch below.
        local probe
        case "$PKG_MGR" in
            apt) probe="apt-get" ;;
            *)   probe="$PKG_MGR" ;;
        esac
        if ! command -v "$probe" >/dev/null 2>&1; then
            echo "Package manager '$PKG_MGR' was specified but '$probe' is not available on this system." >&2
            exit 1
        fi
        return 0
    fi

    if command -v apt-get >/dev/null 2>&1; then
        PKG_MGR="apt"
    elif command -v dnf >/dev/null 2>&1; then
        PKG_MGR="dnf"
    elif command -v pacman >/dev/null 2>&1; then
        PKG_MGR="pacman"
    elif command -v zypper >/dev/null 2>&1; then
        PKG_MGR="zypper"
    else
        # No supported PM found; callers (update_and_install_packages) are
        # responsible for emitting a useful message and exiting.
        PKG_MGR=""
    fi
}


# =============================================================================
# Python version discovery
# =============================================================================
# Minimum version constant: Python >= $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR
# (currently 3.8, set in the globals section above).
#
# resolve_python: locate a Python interpreter on PATH that satisfies the
# minimum version requirement and set the global PYTHON_CMD to its absolute
# path (or bare command name).  Candidates are probed in descending version
# order (python3.13 … python3.8) so the newest qualifying interpreter is
# preferred, followed by the unversioned `python3` and `python` as last
# resorts.  Each candidate is validated by running a one-liner that prints
# major.minor and comparing the output against the minimum.
#
# This function is called:
#   - AFTER system package installation, to lock in the interpreter that was
#     just installed.
#   - AFTER --skip-package-install / --setup-db, to validate the pre-existing
#     interpreter.
#
# Exits with status 1 if no qualifying interpreter is found, since the script
# cannot create a venv, install dependencies, or start the node without one.
resolve_python() {
    local candidates=()
    local v
    # Build a list from python3.13 down to python3.$MIN_PYTHON_MINOR.
    for (( v = 13; v >= MIN_PYTHON_MINOR; v-- )); do
        candidates+=("python3.$v")
    done
    # Unversioned fallbacks (whatever `python3` / `python` happen to point at).
    candidates+=("python3" "python")

    local cmd py_version py_major py_minor
    for cmd in "${candidates[@]}"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            continue
        fi
        py_version=$( "$cmd" -c \
            'import sys; print("{}.{}".format(sys.version_info.major, sys.version_info.minor))' \
            2>/dev/null ) || continue
        py_major="${py_version%%.*}"
        py_minor="${py_version#*.}"
        if (( py_major > MIN_PYTHON_MAJOR )) || \
           { (( py_major == MIN_PYTHON_MAJOR )) && (( py_minor >= MIN_PYTHON_MINOR )); }; then
            PYTHON_CMD="$cmd"
            return 0
        fi
    done

    echo "" >&2
    echo "Error: No Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} interpreter found on PATH." >&2
    echo "Searched for: ${candidates[*]}" >&2
    echo "" >&2
    echo "Install a qualifying Python version or add it to PATH, then re-run this script." >&2
    exit 1
}


# _python_pkgs_for_version: emit the complete set of Python packages required
# for a specific minor version on the detected package manager.  This is a
# private helper for determine_python_packages; callers outside that function
# should not invoke it directly.
#
# Args:
#   $1 minor  Python minor version number (e.g. 12 for Python 3.12).
#
# Each package manager family has its own naming convention:
#
#   apt (Debian/Ubuntu):
#     python3.X, python3.X-dev, python3.X-venv, python3-pip
#     Note: Debian strips the venv module into its own package, so
#     python3.X-venv is mandatory.  pip is shared across all installed
#     Pythons via python3-pip.
#
#   dnf (Fedora/RHEL):
#     python3.X, python3.X-devel, python3.X-pip
#     Fedora bundles venv with the interpreter; pip is versioned.
#
#   zypper (openSUSE):
#     python3XX, python3XX-devel, python3XX-pip, python3XX-virtualenv
#     openSUSE omits the dot in versioned package names.  Both pip and the
#     virtualenv/venv module are shipped as versioned packages; the base
#     python3XX package does NOT include venv on its own.
#
# pacman (Arch) is never reached because determine_python_packages does not
# probe versions for Arch (it ships a single, always-current `python`).
_python_pkgs_for_version() {
    local minor="$1"
    case "$PKG_MGR" in
        apt)    echo "python3.${minor} python3.${minor}-dev python3.${minor}-venv python3-pip" ;;
        dnf)    echo "python3.${minor} python3.${minor}-devel python3.${minor}-pip" ;;
        zypper) echo "python3${minor} python3${minor}-devel python3${minor}-pip python3${minor}-virtualenv" ;;
    esac
}


# _is_pkg_available: return success (0) when the named package is available
# in the configured repositories for the detected package manager, failure
# (1) otherwise.  Used by determine_python_packages to validate every
# companion package before committing to a version.
_is_pkg_available() {
    local pkg="$1"
    case "$PKG_MGR" in
        apt)    apt-cache show "$pkg" >/dev/null 2>&1 ;;
        dnf)    dnf info "$pkg"      >/dev/null 2>&1 ;;
        zypper) zypper info "$pkg"   >/dev/null 2>&1 ;;
        *)      return 1 ;;
    esac
}


# determine_python_packages: emit the list of distribution-specific Python
# packages to install for the detected package manager.  The function selects
# the highest Python version (3.13 … 3.8) where ALL required companion
# packages can be satisfied, preferring versions that are already installed
# on the local system.
#
# Two-pass strategy:
#
#   Pass 1 – Local package database (fast).
#     For each candidate version (highest first), check whether its full
#     package set is already installed using is_package_installed (queries
#     rpm -q, dpkg-query, or pacman -Qi — near-instant).  Three outcomes:
#
#       a) ALL packages installed → return immediately (zero repo calls).
#       b) SOME packages installed → verify only the missing packages
#          against the repo via _is_pkg_available.  If all missing ones
#          are available, return this version (minimal repo calls).
#       c) No packages installed for this version → skip to next.
#
#   Pass 2 – Repository metadata (slow).
#     Reached only when no candidate version has any packages installed
#     locally.  This is the first-install case.  Each candidate's full
#     package set is checked against the repo via _is_pkg_available.
#
# This "all-or-nothing" check is critical because distributions do not
# always ship every companion package for every Python version.  For
# example, openSUSE may have `python312` and `python312-devel` but not
# `python312-pip`; selecting that version would leave the script unable to
# run pip.  By verifying the full set up front, we guarantee the install
# phase will not partially succeed.
#
# Strategy per package manager:
#   apt    – python3.X, python3.X-dev, python3.X-venv, and python3-pip.
#   dnf    – python3.X, python3.X-devel, and python3.X-pip.
#   pacman – Arch always ships a single up-to-date `python` which is >= 3.8,
#            so no probing is necessary.
#   zypper – python3XX, python3XX-devel, python3XX-pip, and
#            python3XX-virtualenv (openSUSE omits the dot in versioned
#            names).
#
# Outputs the space-separated package list to stdout (consumed by
# required_packages).
determine_python_packages() {
    local v

    case "$PKG_MGR" in
        pacman)
            # Arch ships a single `python` package that is always current and
            # well above the 3.8 floor.  No probing is needed.
            echo "python python-pip"
            return
            ;;
    esac

    # ------------------------------------------------------------------
    # Pass 1: fast local-database check (is_package_installed is instant).
    # ------------------------------------------------------------------
    for (( v = 13; v >= MIN_PYTHON_MINOR; v-- )); do
        local candidate_pkgs
        candidate_pkgs=$(_python_pkgs_for_version "$v")
        [ -z "$candidate_pkgs" ] && continue

        local all_installed=true
        local some_installed=false
        local missing_pkgs=()
        local p
        for p in $candidate_pkgs; do
            if is_package_installed "$p"; then
                some_installed=true
            else
                all_installed=false
                missing_pkgs+=("$p")
            fi
        done

        # (a) Every package for this version is already installed.
        if $all_installed; then
            echo "$candidate_pkgs"
            return
        fi

        # (b) Some packages are installed; verify the missing ones are
        #     available in the repo before committing to this version.
        #     This path makes only as many repo calls as there are missing
        #     packages (typically 1-2), not the full set.
        if $some_installed; then
            local all_missing_available=true
            for p in "${missing_pkgs[@]}"; do
                if ! _is_pkg_available "$p"; then
                    all_missing_available=false
                    break
                fi
            done
            if $all_missing_available; then
                echo "$candidate_pkgs"
                return
            fi
            # Some missing packages are not available; this version cannot
            # be fully satisfied.  Fall through to try the next version.
        fi
    done

    # ------------------------------------------------------------------
    # Pass 2: slow repo probe (first-install case — nothing is installed).
    # ------------------------------------------------------------------
    for (( v = 13; v >= MIN_PYTHON_MINOR; v-- )); do
        local candidate_pkgs
        candidate_pkgs=$(_python_pkgs_for_version "$v")
        [ -z "$candidate_pkgs" ] && continue

        local all_available=true
        local p
        for p in $candidate_pkgs; do
            if ! _is_pkg_available "$p"; then
                all_available=false
                break
            fi
        done

        if $all_available; then
            echo "$candidate_pkgs"
            return
        fi
    done

    # Fallback: no fully-satisfiable versioned set was found; emit the
    # generic package names and hope the distribution's default python3
    # meets the minimum version requirement.  resolve_python will catch
    # the failure later if it does not.
    case "$PKG_MGR" in
        apt)    echo "python3 python3-dev python3-venv python3-pip" ;;
        dnf)    echo "python3 python3-devel python3-pip" ;;
        zypper) echo "python3 python3-devel python3-pip" ;;
        *)      echo "" ;;
    esac
}


# Echo the list of required system packages for the detected package manager.
#
# Arg 1 (boolean): include Python packages (true|false). When false, only the
# packages required for the database setup are returned. The caller passes
# false when SETUP_DB_ONLY is true so the Python toolchain (python3, headers,
# venv) is omitted from the install list and the operator can manage Python
# independently (e.g. via pyenv or asdf).
required_packages() {
    local include_python="$1"
    local base_pkgs python_pkgs
    case "$PKG_MGR" in
        apt)
            base_pkgs="gcc libgmp-dev libpq-dev postgresql"
            ;;
        dnf)
            base_pkgs="gcc gmp-devel libpq-devel postgresql-server"
            ;;
        pacman)
            base_pkgs="gcc gmp postgresql-libs postgresql"
            ;;
        zypper)
            base_pkgs="gcc gmp-devel postgresql-devel postgresql-server libexpat1"
            ;;
        *)
            echo ""
            return
            ;;
    esac

    if [ "$include_python" = true ]; then
        python_pkgs=$(determine_python_packages)
        echo "$base_pkgs $python_pkgs"
    else
        echo "$base_pkgs"
    fi
}


# Return success (0) if the named package is installed, failure (1) otherwise.
is_package_installed() {
    local pkg="$1"
    case "$PKG_MGR" in
        apt)
            # `dpkg-query -W` exits 0 even when the package is unknown to
            # dpkg, so the install state must be inspected by parsing the
            # Status field. "install ok installed" is the only state that
            # represents a fully-installed package.
            dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"
            ;;
        dnf|zypper)
            # Both Fedora/RHEL and openSUSE use rpm under the hood, so a
            # single `rpm -q` check is sufficient for both managers.
            rpm -q "$pkg" >/dev/null 2>&1
            ;;
        pacman)
            # `pacman -Q` queries the local install database; -i requests
            # info on the package. Non-zero exit means it is not installed.
            pacman -Qi "$pkg" >/dev/null 2>&1
            ;;
        *)
            return 1
            ;;
    esac
}


# Refresh the local package metadata for the detected package manager.
#
# Only apt requires an explicit refresh before install. dnf and zypper
# transparently refresh their metadata during install. pacman is intentionally
# omitted: running `pacman -Sy` followed by an install of a subset of packages
# is the canonical "partial upgrade" pitfall on Arch and can yield a broken
# dependency graph. The script trusts the user's existing pacman sync
# database; if it is too stale, the install will fail with a clear message
# and the user can run `pacman -Syu` themselves.
update_package_lists() {
    case "$PKG_MGR" in
        apt) sudo apt update ;;
    esac
}


# Install the given list of packages using the detected package manager.
# Honors SKIP_PROMPTS by passing the appropriate non-interactive flag.
#
# Per-PM non-interactive flags:
#   apt:    -y assumes "yes" to all prompts.
#   dnf:    -y same semantics as apt.
#   pacman: --noconfirm bypasses both the install confirmation and any
#           conflict-resolution prompts. --needed is always passed (also in
#           interactive mode) to prevent reinstalling current packages.
#   zypper: --non-interactive is a global flag preceding the subcommand;
#           it assumes "yes" to all prompts.
install_packages_via_pm() {
    local packages=("$@")
    case "$PKG_MGR" in
        apt)
            if $SKIP_PROMPTS; then
                sudo apt install -y "${packages[@]}"
            else
                sudo apt install "${packages[@]}"
            fi
            ;;
        dnf)
            if $SKIP_PROMPTS; then
                sudo dnf install -y "${packages[@]}"
            else
                sudo dnf install "${packages[@]}"
            fi
            ;;
        pacman)
            if $SKIP_PROMPTS; then
                sudo pacman -S --noconfirm --needed "${packages[@]}"
            else
                sudo pacman -S --needed "${packages[@]}"
            fi
            ;;
        zypper)
            if $SKIP_PROMPTS; then
                sudo zypper --non-interactive install "${packages[@]}"
            else
                sudo zypper install "${packages[@]}"
            fi
            ;;
    esac
}


# Top-level entry point for system package installation.
#
# Flow:
#   1. Resolve which package manager to use (detect_package_manager handles
#      both auto-detection and validation of a --package-manager override).
#   2. Refresh the local package database (apt only; see update_package_lists).
#   3. Compute which required packages are missing, to avoid re-prompting on
#      packages that are already current.
#   4. Install the missing packages, exiting on failure.
#
# Exits with status 1 if no supported package manager is detected, since the
# script cannot make progress without one and silent fallbacks would mask
# legitimate environmental problems.
update_and_install_packages() {
    detect_package_manager

    if [ -z "$PKG_MGR" ]; then
        echo "No supported package manager (apt, dnf, pacman, zypper) was detected on this system."
        echo "Required system packages must be installed manually before running this script with"
        echo "the --skip-package-install argument. Refer to the README for the list of required packages."
        exit 1
    fi

    echo "Detected package manager: $PKG_MGR"
    echo ""
    echo "Updating package lists..."
    update_package_lists
    echo ""

    echo "Checking required system packages..."

    # SETUP_DB_ONLY runs before the venv / pip stages, so the Python toolchain
    # is not needed in that mode and is excluded from the install set.
    local include_python=true
    if $SETUP_DB_ONLY; then
        include_python=false
    fi

    local required
    required=$(required_packages "$include_python")
    local packages_to_install=()

    for package in $required; do
        if is_package_installed "$package"; then
            echo "Package $package is already installed."
        else
            echo "Package $package is not installed."
            packages_to_install+=("$package")
        fi
    done

    if [ ${#packages_to_install[@]} -gt 0 ]; then
        echo ""
        echo "Installing required packages: ${packages_to_install[*]}"
        echo ""
        install_packages_via_pm "${packages_to_install[@]}" || { echo ""; echo "Package installation failed"; exit 1; }
        echo ""
        echo "Package installation complete."
    else
        echo ""
        echo "All required system packages are already installed."
    fi

    # zypper-specific: update libexpat1 to avoid a symbol mismatch that
    # breaks the pyexpat C extension module shipped with versioned Python
    # packages.  Without this, `python3.X -m venv` and `virtualenv` fail
    # with:
    #   ImportError: …pyexpat…: undefined symbol:
    #       XML_SetAllocTrackerActivationThreshold
    # `zypper install` alone does not upgrade already-installed packages;
    # an explicit `zypper update` is required.
    if [[ "$PKG_MGR" == "zypper" ]] && $include_python; then
        echo ""
        echo "Ensuring libexpat1 is up-to-date (zypper)..."
        if $SKIP_PROMPTS; then
            sudo zypper --non-interactive update libexpat1 2>/dev/null || true
        else
            sudo zypper update libexpat1 2>/dev/null || true
        fi
    fi
}


# =============================================================================
# PostgreSQL service / configuration helpers
# =============================================================================
# Run a service command (start/restart) for the postgresql unit.
#
# systemctl is preferred on every modern distribution because it talks
# directly to systemd. The legacy `service` wrapper is kept as a fallback
# for environments without systemd (notably WSL2 with the default
# configuration, and older sysvinit-based images). When systemctl is present
# but the call fails (e.g. WSL2 without --systemd enabled), execution falls
# through to the service-based fallback rather than exiting.
manage_postgresql_service() {
    local action="$1"
    if command -v systemctl >/dev/null 2>&1; then
        if sudo systemctl "$action" postgresql 2>/dev/null; then
            return 0
        fi
    fi
    if command -v service >/dev/null 2>&1; then
        sudo service postgresql "$action"
        return $?
    fi
    return 1
}


# Initialize the PostgreSQL data directory (when required by the distribution)
# and ensure the postgresql service is running.
#
# Debian-based systems automatically create the data directory and start the
# service during the package install; other distributions require an explicit
# initdb step before the systemd unit will start. Each branch is tailored to
# the conventions of its distribution family.
initialize_postgres_service() {
    case "$PKG_MGR" in
        apt)
            # Debian-family postinstall scripts run initdb and start the
            # service via /etc/init.d/postgresql. The start call here is a
            # defensive no-op for the common case and a recovery for boots
            # where the service did not auto-start (e.g. inside a container
            # that was committed before the service was up).
            manage_postgresql_service start >/dev/null 2>&1 || true
            ;;
        dnf)
            # On Fedora/RHEL, the postgresql-server package does NOT
            # initialize the cluster or start the service. The data directory
            # must be bootstrapped explicitly with `postgresql-setup --initdb`
            # before the systemd unit will start.
            if [ ! -d "/var/lib/pgsql/data/base" ]; then
                echo "Initializing PostgreSQL data directory..."
                # Some Fedora/RHEL releases place postgresql-setup outside
                # the default sudo PATH; try the absolute path as a fallback.
                sudo postgresql-setup --initdb >/dev/null 2>&1 \
                    || sudo /usr/bin/postgresql-setup --initdb >/dev/null 2>&1 \
                    || { echo "PostgreSQL initdb failed"; exit 1; }
            fi
            if ! manage_postgresql_service start; then
                echo "Failed to start PostgreSQL service"
                exit 1
            fi
            ;;
        pacman)
            # On Arch, the postgresql package does NOT initialize the cluster.
            # initdb must run as the postgres OS user with a login shell so
            # HOME and PATH are correct (postgres' home is /var/lib/postgres).
            # `sudo -iu postgres` provides exactly that environment.
            if [ ! -d "/var/lib/postgres/data/base" ]; then
                echo "Initializing PostgreSQL data directory..."
                sudo -iu postgres initdb -D /var/lib/postgres/data >/dev/null 2>&1 \
                    || { echo "PostgreSQL initdb failed"; exit 1; }
            fi
            if ! manage_postgresql_service start; then
                echo "Failed to start PostgreSQL service"
                exit 1
            fi
            ;;
        zypper)
            # On openSUSE, the postgresql.service unit's PreStart phase will
            # run initdb on first boot if the data directory is missing. We
            # still attempt postgresql-setup --initdb opportunistically for
            # versions that don't ship the auto-init, treating its failure as
            # benign (the systemd unit will handle it on start).
            if [ -d "/var/lib/pgsql" ] && [ ! -d "/var/lib/pgsql/data/base" ]; then
                if command -v postgresql-setup >/dev/null 2>&1; then
                    sudo postgresql-setup --initdb >/dev/null 2>&1 || true
                fi
            fi
            if ! manage_postgresql_service start; then
                echo "Failed to start PostgreSQL service"
                exit 1
            fi
            ;;
        *)
            # Unknown / unsupported PM: assume the operator has installed and
            # started PostgreSQL manually before invoking the script.
            ;;
    esac
}


# Restart the PostgreSQL service. Used after pg_hba.conf modifications to
# pick up the new authentication settings.
restart_postgresql_service() {
    if ! manage_postgresql_service restart; then
        echo "PostgreSQL restart failed"
        exit 1
    fi
}


# Wait for the PostgreSQL cluster to accept connections.
#
# Modern systemd units for postgresql declare Type=notify, so a successful
# `systemctl restart` already implies the cluster is ready. However, some
# distributions, container images, and the legacy SysV / `service` fallback
# return as soon as the process is running, before the postmaster is
# accepting connections. Adding an explicit readiness wait closes the small
# race window between restart_postgresql_service returning and the schema
# import psql call.
#
# Strategy:
#   - Prefer pg_isready, the canonical readiness probe shipped with the
#     PostgreSQL client tools. It is fast, side-effect free, and returns 0
#     only when the server is ready to accept connections.
#   - Fall back to a trivial superuser query via `sudo -u postgres psql`
#     when pg_isready is not on PATH (rare, but possible on minimal images
#     that install only postgresql-server without the client tools).
#
# Bounded by `timeout` seconds; a longer-than-expected startup is reported
# as a warning rather than a hard failure, because the schema import that
# follows will surface a clear error of its own if the server is genuinely
# unreachable.
wait_for_postgresql_ready() {
    local timeout=30
    local elapsed=0
    if command -v pg_isready >/dev/null 2>&1; then
        while ! pg_isready -q >/dev/null 2>&1; do
            sleep 1
            elapsed=$((elapsed + 1))
            if (( elapsed >= timeout )); then
                echo "Warning: PostgreSQL did not report ready within ${timeout}s; continuing anyway."
                return 1
            fi
        done
        return 0
    fi
    while ! sudo -u postgres psql -X -A -t -c "SELECT 1;" >/dev/null 2>&1; do
        sleep 1
        elapsed=$((elapsed + 1))
        if (( elapsed >= timeout )); then
            echo "Warning: PostgreSQL did not become responsive within ${timeout}s; continuing anyway."
            return 1
        fi
    done
    return 0
}


# Locate the active pg_hba.conf file.
#
# The authoritative location is whatever the running cluster was started with
# (it can be moved via postgresql.conf or a command-line override). Querying
# the running instance for its hba_file via SHOW avoids the per-distribution
# path guesswork and remains correct under custom configurations.
#
# The fallback list covers the standard locations for the supported package
# managers: /etc/postgresql/<version>/main on Debian-family, /var/lib/pgsql
# on RPM-family, and /var/lib/postgres on Arch.
find_pg_hba_conf() {
    local conf
    conf=$(sudo -u postgres psql -tAc "SHOW hba_file;" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$conf" ] && [ -f "$conf" ]; then
        echo "$conf"
        return 0
    fi
    local candidate
    for candidate in \
        /etc/postgresql/*/main/pg_hba.conf \
        /var/lib/pgsql/data/pg_hba.conf \
        /var/lib/postgres/data/pg_hba.conf; do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}


# =============================================================================
# Interactive helpers
# =============================================================================
# Read a password from stdin while echoing asterisks for each typed character.
#
# Disables tty echo, reads one character at a time, and re-implements the
# minimal line editing needed for password entry: backspace deletes one
# character (with a visible " " erase), Enter terminates input. The result
# is written into the named variable using `printf -v` so values containing
# any special characters (single quotes, slashes, backslashes, dollars) are
# stored verbatim without quoting hazards.
#
# Trap handling:
#   The function snapshots any INT and EXIT traps installed by the parent
#   (using `trap -p`, which emits each as a re-installable command) and
#   restores them verbatim on return. This avoids permanently stripping
#   parent-level traps when nested under callers that install their own.
#   While the function is active the trap restores the tty on Ctrl+C and
#   re-raises with exit 130 to match shell conventions.
#
# Args:
#   $1 prompt   Prompt text shown before input.
#   $2 var_name Name of the variable that will receive the typed password.
read_password_with_asterisks() {
    local prompt=$1
    local var_name=$2

    if $SKIP_PROMPTS || $USE_DEFAULT_ENV_VARS; then
        return 0
    fi

    printf '%s ' "$prompt"

    local password=""
    local char_count=0

    # Snapshot any pre-existing parent traps so we can restore them verbatim
    # on return. `trap -p SIG` emits an empty string when no trap is set.
    local saved_trap_int saved_trap_exit
    saved_trap_int=$(trap -p INT)
    saved_trap_exit=$(trap -p EXIT)

    local saved_settings
    saved_settings=$(stty -g)

    # On INT, restore tty and re-raise (exit 130 mirrors shell convention).
    # The EXIT handler is a fail-safe in case the function is short-circuited.
    trap 'stty "$saved_settings" 2>/dev/null; exit 130' INT
    trap 'stty "$saved_settings" 2>/dev/null' EXIT

    stty -echo

    local char
    while IFS= read -r -s -n1 char; do
        # Enter (newline) signals end of input.
        if [[ $char == $'\0' ]]; then
            break
        fi
        # Backspace (DEL, 0x7F): erase the last character if any.
        if [[ $char == $'\177' ]]; then
            if [ $char_count -gt 0 ]; then
                char_count=$((char_count-1))
                password="${password%?}"
                # Move cursor back, overwrite with space, move back again.
                printf '\b \b'
            fi
            continue
        fi
        printf '*'
        char_count=$((char_count+1))
        password+="$char"
    done

    stty "$saved_settings"

    # Restore parent traps verbatim. If no parent trap was set, clear ours.
    if [ -n "$saved_trap_int" ]; then
        eval "$saved_trap_int"
    else
        trap - INT
    fi
    if [ -n "$saved_trap_exit" ]; then
        eval "$saved_trap_exit"
    else
        trap - EXIT
    fi

    printf '\n'

    printf -v "$var_name" '%s' "$password"
}


# Validate user input as a TCP port number (1..65535). Falls back to either
# the current value from the .env file or the script default if input is
# blank. Re-prompts on invalid input rather than rejecting outright.
#
# Args:
#   $1 prompt            Prompt text.
#   $2 var_name          Name of the variable to update.
#   $3 show_current_vars When true, the fallback is the current .env value;
#                        otherwise the fallback is the script default for
#                        this variable.
validate_port_input() {
    local prompt="$1"
    local var_name="$2"
    local show_current_vars="$3"
    local input_port=""
    local fallback

    if $show_current_vars; then
        fallback=$(read_env_variable "$var_name")
    else
        fallback="${!var_name}"
    fi

    while true; do
        read -p "$prompt " input_port
        if [[ -z "$input_port" ]]; then
            input_port="$fallback"
            # If the fallback is also blank, fall through to the validators
            # below by using the script-level default explicitly.
            if [[ -z "$input_port" ]]; then
                input_port="3006"
            fi
            # Validate the resolved fallback. A corrupted .env value (e.g.
            # "abc" or "99999") must not bypass validation just because the
            # user accepted the default with Enter; snap such a value to the
            # script-level default 3006 rather than persist it untouched.
            # The final guard in update_variable provides defense in depth
            # for DENARO_NODE_PORT specifically; this loop's correctness
            # also matters for any future port-typed variable that reuses
            # validate_port_input.
            if ! [[ "$input_port" =~ ^[0-9]+$ ]] || (( input_port < 1 || input_port > 65535 )); then
                input_port="3006"
            fi
            break
        elif ! [[ "$input_port" =~ ^[0-9]+$ ]]; then
            echo "Invalid input. Port must be a number."
            echo ""
        elif (( input_port < 1 || input_port > 65535 )); then
            echo "Invalid port number. Port must be between 1 and 65535."
            echo ""
        else
            break
        fi
    done
    printf -v "$var_name" '%s' "$input_port"
}


# Update or append a single variable's value in the .env file.
#
# Honors SKIP_PROMPTS and USE_DEFAULT_ENV_VARS for non-interactive operation,
# and special-cases password and port inputs (the former needs masked input
# with confirmation; the latter needs numeric range validation).
#
# Args:
#   $1 prompt             Human-readable prompt text shown before the input.
#   $2 var_name           Name of the global variable to update.
#   $3 show_current_vars  When true, treat the current .env value as the
#                         fallback instead of the script default.
update_variable() {
    local prompt="$1"
    local var_name="$2"
    local show_current_vars="$3"

    # Script-level default (set in the globals block above). Captured before
    # any reassignment so non-interactive paths still have a valid fallback.
    local default_value="${!var_name}"

    # `var_value` is the value the prompt suggests to the user, which differs
    # depending on whether we are showing current values (update mode) or
    # script defaults (initial setup mode).
    local var_value
    local prompt_value_string
    if $show_current_vars; then
        var_value=$(read_env_variable "$var_name")
        prompt_value_string="current:"
    else
        var_value="${!var_name}"
        prompt_value_string="default:"
    fi

    # Create a display-safe version of var_value so blank defaults show as "None"
    local display_value="$var_value"
    if [[ -z "$display_value" ]]; then
        display_value="None"
    fi

    # Current value as it presently exists in the .env file (if any). Used by
    # the non-interactive branch to decide whether a fill-in is required.
    local current_value
    current_value=$(read_env_variable "$var_name")

    if ! $SKIP_PROMPTS && ! $USE_DEFAULT_ENV_VARS; then
        if [[ "$var_name" == "POSTGRES_PASSWORD" ]]; then
            # Password input: read masked, then ask for confirmation. Loop
            # until both entries match and the password is non-empty.
            #
            # Update-mode special case: when an existing non-empty password
            # is on disk and we are running with show_current_vars=true, an
            # empty entry on the first prompt preserves the stored value.
            # This makes the password obey the same "leave blank to keep
            # current value" contract advertised for every other variable
            # in the update flow, without ever displaying the secret.
            local pw_prompt="$prompt:"
            if $show_current_vars && [[ -n "$current_value" ]]; then
                pw_prompt="$prompt (leave blank to keep current):"
            fi
            while true; do
                read_password_with_asterisks "$pw_prompt" "$var_name"
                if [[ -z "${!var_name}" ]] && $show_current_vars && [[ -n "$current_value" ]]; then
                    # Restore the on-disk value into the shell global so the
                    # subsequent write_env_var call is a no-op rewrite and
                    # the change-detection logic in set_env_variables sees
                    # an unchanged value (no spurious db_pass_changed=true).
                    printf -v "$var_name" '%s' "$current_value"
                    break
                fi
                if [[ -z "${!var_name}" ]]; then
                    echo "Password can not be empty, please try again."
                    echo ""
                    continue
                fi
                local password_value_1
                password_value_1=$(printf '%s' "${!var_name}" | sha256sum | cut -d' ' -f1)
                read_password_with_asterisks "Confirm database password:" "$var_name"
                local password_value_2
                password_value_2=$(printf '%s' "${!var_name}" | sha256sum | cut -d' ' -f1)
                # Compare hashes rather than the raw passwords so the
                # cleartext is never echoed (even via process listings).
                if [[ "$password_value_1" != "$password_value_2" ]]; then
                    echo "Passwords do not match, please try again."
                    echo ""
                else
                    break
                fi
            done

        elif [[ "$var_name" == "DENARO_NODE_PORT" ]]; then
            # Port input: numeric range validation handled by the helper.
            validate_port_input "$prompt ($prompt_value_string $display_value):" "$var_name" "$show_current_vars"

        else
            # Generic input: a blank entry preserves the current/default value.
            local value
            read -p "$prompt ($prompt_value_string $display_value): " value
            if [[ -z "$value" ]]; then
                if $show_current_vars; then
                    value="$var_value"
                else
                    value="$default_value"
                fi
            fi
            printf -v "$var_name" '%s' "$value"
        fi

    elif [[ -z "$current_value" ]]; then
        # Non-interactive mode: only fill in a value if the .env entry is
        # absent or blank, so pre-existing values are preserved.
        if $show_current_vars; then
            printf -v "$var_name" '%s' "$var_value"
        else
            printf -v "$var_name" '%s' "$default_value"
        fi
    fi

    # Final sanity guard: certain variables must not reach the .env file with
    # a value that would crash a downstream stage. Apply uniformly so the
    # same guard runs in interactive, --skip-prompts, and USE_DEFAULT_ENV_VARS
    # paths and so a malformed pre-existing .env value cannot survive a
    # non-interactive re-run. Currently scoped to DENARO_NODE_PORT, where an
    # out-of-range value is silently snapped to the script default 3006.
    if [[ "$var_name" == "DENARO_NODE_PORT" ]]; then
        local _candidate_port="${!var_name}"
        if ! [[ "$_candidate_port" =~ ^[0-9]+$ ]] || (( _candidate_port < 1 || _candidate_port > 65535 )); then
            printf -v "$var_name" '%s' "3006"
        fi
    fi

    # Persist the resulting value into the .env file via the safe writer.
    write_env_var "$var_name" "${!var_name}" "$env_file"
}


# Top-level driver for .env file configuration.
#
# Three high-level branches:
#   1. .env exists and is complete: ask the user (or assume "no" under
#      SKIP_PROMPTS) whether to update it. Yes -> walk every variable showing
#      current values; No -> values are already loaded into globals via the
#      load_env_variables call at the top of this function and we return.
#   2. .env exists but is incomplete: walk only the missing variables, using
#      the (already loaded) current values as fallbacks. Existing variables
#      remain untouched and their values, loaded into shell globals at the
#      top of the function, are used by setup_database.
#   3. .env does not exist: create an empty file and walk every variable.
#
# Tracks db_user_changed / db_pass_changed / db_name_changed via SHA-256
# hashes of the pre- and post-edit values so setup_database can re-apply
# the password when needed and surface a warning if the user has renamed
# the role or database.
set_env_variables() {
    echo ""
    echo "Starting dotenv configuration..."
    echo ""

    local PROMPT_FOR_DEFAULT=true
    local show_current_vars=false

    # Always seed shell globals from the existing .env first. This guarantees
    # that variables which are present-and-complete in .env are reflected in
    # the shell globals consumed by setup_database, even when only a subset
    # of the variables is missing or being updated.
    load_env_variables

    local missing_vars=()
    if [[ -f "$env_file" ]]; then
        echo "$env_file file already exists."
        echo ""

        # Recompute missing vars from disk (accurate after load_env_variables).
        # shellcheck disable=SC2207
        missing_vars=($(identify_missing_variables "$env_file"))

        if [ ${#missing_vars[@]} -eq 0 ]; then
            # .env is complete; confirm whether to update it.
            if ! $SKIP_PROMPTS; then
                local update_choice
                prompt_yes_no "Do you want to update the current configuration? (y/n):" update_choice
                case "$update_choice" in
                    y)
                        show_current_vars=true
                        PROMPT_FOR_DEFAULT=false
                        # On a confirmed update, re-prompt every variable.
                        missing_vars=("${ENV_VARS[@]}")
                        echo "Leave blank to keep the current value."
                        echo ""
                        ;;
                    n)
                        echo "Keeping current configuration."
                        return 0
                        ;;
                esac
            else
                # Non-interactive: keep existing complete config as-is.
                echo "Keeping current configuration."
                return 0
            fi
        else
            echo "The .env file is incomplete or has empty values."
            echo "Missing variables: ${missing_vars[*]}"
            echo ""
            PROMPT_FOR_DEFAULT=true
        fi
    else
        echo "$env_file file does not exist."
        echo "Proceeding with configuration..."
        echo ""
        PROMPT_FOR_DEFAULT=true
        # Truncate (or create) the .env file before populating it.
        > "$env_file"
        # All variables are missing in a fresh file.
        missing_vars=("${ENV_VARS[@]}")
    fi

    if ! $SKIP_PROMPTS; then
        if $PROMPT_FOR_DEFAULT; then
            local use_defaults
            prompt_yes_no "Do you want to use the default values for configuration? (y/n):" use_defaults
            case "$use_defaults" in
                y)
                    USE_DEFAULT_ENV_VARS=true
                    echo "Using default values for configuration."
                    ;;
                n)
                    USE_DEFAULT_ENV_VARS=false
                    echo "Leave blank to use the default value."
                    echo ""
                    ;;
            esac
        else
            USE_DEFAULT_ENV_VARS=false
        fi
    else
        USE_DEFAULT_ENV_VARS=true
        echo "Using default values for configuration."
    fi

    # Snapshot the database identity values so we can detect changes after
    # the user finishes editing.
    #
    # We track BOTH the raw value (to know whether the variable was already
    # set to a non-empty value) AND, for the password, a SHA-256 hash so we
    # do not retain the cleartext longer than necessary. A variable is
    # treated as "changed" only if it was previously non-empty AND the new
    # value differs from the prior one. This prevents a fresh install
    # (initial value empty) from being misreported as a rename, which would
    # otherwise surface a misleading "previous role/database remains in the
    # cluster" warning during database setup.
    local initial_db_user_value
    local initial_db_name_value
    local initial_db_pass_was_set=false
    local initial_db_pass_hash=""
    initial_db_user_value=$(read_env_variable "POSTGRES_USER")
    initial_db_name_value=$(read_env_variable "DENARO_DATABASE_NAME")
    local _initial_pass_value
    _initial_pass_value=$(read_env_variable "POSTGRES_PASSWORD")
    if [[ -n "$_initial_pass_value" ]]; then
        initial_db_pass_was_set=true
        initial_db_pass_hash=$(printf '%s' "$_initial_pass_value" | sha256sum | cut -d' ' -f1)
    fi
    unset _initial_pass_value

    # Walk each variable that is missing (or every variable in update mode).
    local v
    for v in "${ENV_VARS[@]}"; do
        if [[ " ${missing_vars[*]} " == *" $v "* ]]; then
            local prompt_text
            case "$v" in
                POSTGRES_USER)            prompt_text="Enter database username" ;;
                POSTGRES_PASSWORD)        prompt_text="Enter password for database user" ;;
                DENARO_DATABASE_NAME)     prompt_text="Enter database name" ;;
                DENARO_DATABASE_HOST)     prompt_text="Enter database host" ;;
                DENARO_NODE_HOST)         prompt_text="Enter local Denaro node address or hostname" ;;
                DENARO_NODE_PORT)         prompt_text="Enter local Denaro node port" ;;
                DENARO_SELF_URL)          prompt_text="Enter the public address of this Denaro node (e.g., https://yourdomain.com). Leave blank if the node is private" ;;
                DENARO_BOOTSTRAP_NODE)    prompt_text="Enter the address of a main Denaro node to sync with" ;;
                LOG_LEVEL)                prompt_text="Enter the log level for the Denaro node (DEBUG, INFO, WARNING, ERROR, CRITICAL)" ;;
                LOG_CONSOLE_HIGHLIGHTING) prompt_text="Enable log highlighting? (True/False)" ;;
                *)                        prompt_text="Enter value for $v" ;;
            esac
            update_variable "$prompt_text" "$v" "$show_current_vars"
        fi
    done

    # Re-snapshot and compare to flag changes for setup_database. A variable
    # is "changed" only if it had a non-empty initial value AND the new
    # value differs. The empty-to-non-empty transition (fresh install) is
    # NOT a change and must not trigger the post-setup_database "previous
    # role/database remains" warning.
    local new_db_user_value new_db_name_value
    new_db_user_value=$(read_env_variable "POSTGRES_USER")
    new_db_name_value=$(read_env_variable "DENARO_DATABASE_NAME")

    if [[ -n "$initial_db_user_value" && "$initial_db_user_value" != "$new_db_user_value" ]]; then
        db_user_changed=true
    fi
    if [[ -n "$initial_db_name_value" && "$initial_db_name_value" != "$new_db_name_value" ]]; then
        db_name_changed=true
    fi

    # Password change detection covers three operator scenarios:
    #
    #   a) .env had a password before AND that password differs from the
    #      new one: classic rotation. db_pass_changed=true forces
    #      setup_database to ALTER USER ... PASSWORD on an already-set role.
    #
    #   b) .env had no password initially (fresh install OR the operator
    #      hand-cleared / hand-deleted the value), but now does. If the
    #      cluster role already exists with a stale password from an
    #      earlier run, the runtime would otherwise fail to authenticate
    #      because setup_database's "rolpassword IS NOT NULL" branch would
    #      skip rotation. Setting db_pass_changed=true here guarantees the
    #      cluster password is re-aligned with the .env value. For the
    #      genuinely-fresh case (role does not yet exist), setup_database
    #      creates the role first and the rolpassword-IS-NULL branch sets
    #      the password unconditionally; the rotation flag is benign.
    #
    #   c) .env had a password before AND the new value matches: hash
    #      compare returns equal, db_pass_changed stays false, no rotation.
    local _new_pass_value
    _new_pass_value=$(read_env_variable "POSTGRES_PASSWORD")
    if $initial_db_pass_was_set; then
        local _new_pass_hash
        _new_pass_hash=$(printf '%s' "$_new_pass_value" | sha256sum | cut -d' ' -f1)
        if [[ "$initial_db_pass_hash" != "$_new_pass_hash" ]]; then
            db_pass_changed=true
        fi
        unset _new_pass_hash
    elif [[ -n "$_new_pass_value" ]]; then
        db_pass_changed=true
    fi
    unset _new_pass_value

    # After write_env_var has potentially rewritten the file, refresh the
    # shell globals so subsequent stages observe the canonical values.
    load_env_variables

    echo ""
    echo "$env_file file configured."
}


# Provision the PostgreSQL database, role, password, privileges, and
# ownership; switch local and loopback authentication to md5 (treating
# md5 and scram-sha-256 as already-good); and import the Denaro schema.
#
# Distribution-specific service initialization is delegated to
# initialize_postgres_service, and the pg_hba.conf path is resolved
# dynamically via find_pg_hba_conf so the same flow works on Debian-based
# and RPM/Arch-based systems.
#
# Each step is idempotent. Two flags coordinate post-provisioning work:
#   - pg_hba_modified: true only when we actually rewrote pg_hba.conf;
#                      drives the PostgreSQL restart.
#   - db_created:      true only when we ran CREATE DATABASE on this
#                      invocation; drives the schema import. The schema is
#                      idempotent (CREATE TABLE IF NOT EXISTS), but tying
#                      the import to fresh database creation avoids spurious
#                      re-imports on every benign re-run.
#
# Validation: POSTGRES_USER and DENARO_DATABASE_NAME are required to be
# valid SQL identifiers ([A-Za-z_][A-Za-z0-9_]*). The role password is
# passed via psql's -v variable substitution (:'name') so quoting is done
# by the SQL parser rather than the shell.
setup_database() {
    local pg_hba_modified=false
    local db_created=false

    # Validate identifier names before any psql command builds SQL by
    # interpolation. This prevents shell-special characters from being
    # injected into SQL and surfaces a clear error early.
    if ! validate_sql_identifier "$POSTGRES_USER"; then
        echo "POSTGRES_USER ('$POSTGRES_USER') must contain only letters, digits, and underscores," >&2
        echo "and must begin with a letter or underscore." >&2
        exit 1
    fi
    if ! validate_sql_identifier "$DENARO_DATABASE_NAME"; then
        echo "DENARO_DATABASE_NAME ('$DENARO_DATABASE_NAME') must contain only letters, digits, and underscores," >&2
        echo "and must begin with a letter or underscore." >&2
        exit 1
    fi

    # When DENARO_DATABASE_HOST does not point at the local machine, the
    # operator is targeting an externally-managed PostgreSQL instance.
    # Provisioning a local cluster, rotating its passwords, and rewriting
    # its pg_hba.conf cannot help that case, and the schema import below
    # would land in the wrong place. Short-circuit with an actionable hint
    # that includes the exact psql command to seed the remote schema.
    #
    # In --skip-prompts mode we always skip the local provisioning step
    # rather than silently provisioning something the operator did not ask
    # for. Interactively, we offer to proceed anyway so an operator who
    # has temporarily set DENARO_DATABASE_HOST to a public DNS name that
    # also points back at this host can still run the local-bring-up flow.
    if ! is_local_db_host "$DENARO_DATABASE_HOST"; then
        echo "Note: DENARO_DATABASE_HOST is set to '$DENARO_DATABASE_HOST', which is not a"
        echo "      local address. This script provisions a LOCAL PostgreSQL cluster;"
        echo "      a remote database must be provisioned and configured separately."
        echo ""
        echo "      To initialise the schema on the remote database, run:"
        echo "        PGPASSWORD='<password>' psql \\"
        echo "          -h '$DENARO_DATABASE_HOST' \\"
        echo "          -U '$POSTGRES_USER' \\"
        echo "          -d '$DENARO_DATABASE_NAME' \\"
        echo "          -f denaro/schema.sql"
        echo ""
        if $SKIP_PROMPTS; then
            echo "Skipping local database provisioning (non-interactive mode)."
            echo ""
            return 0
        fi
        local proceed_choice
        prompt_yes_no "Provision a LOCAL PostgreSQL cluster anyway? (y/n):" proceed_choice
        case "$proceed_choice" in
            n)
                echo "Skipping database setup."
                echo ""
                return 0
                ;;
            y)
                echo "Proceeding with local database provisioning..."
                echo ""
                ;;
        esac
    fi

    # Surface a warning when the operator has renamed the user or database
    # during the .env update step. The script provisions the new identity
    # but leaves the old one in place; cleanup is a manual operation.
    if $db_user_changed; then
        echo "Note: POSTGRES_USER was changed. The new role will be created;"
        echo "      the previous role remains in the cluster and must be"
        echo "      dropped manually if no longer needed."
        echo ""
    fi
    if $db_name_changed; then
        echo "Note: DENARO_DATABASE_NAME was changed. The new database will be"
        echo "      created and the schema re-imported; the previous database"
        echo "      remains in the cluster and must be dropped manually if no"
        echo "      longer needed."
        echo ""
    fi

    # Make sure the package manager is detected before attempting any
    # PostgreSQL service operations. detect_package_manager is idempotent:
    # if PKG_MGR was already set (via update_and_install_packages or via
    # --package-manager), this is a no-op.
    detect_package_manager
    initialize_postgres_service

    echo ""
    echo "Starting Database Setup..."
    echo ""

    # Step 1: Configure pg_hba.conf first.
    # This MUST run before any psql commands to ensure the 'postgres' OS user
    # retains passwordless peer access to the database on all distributions.
    # If a previous run locked out the user, find_pg_hba_conf will use its 
    # fallback paths to find the file and heal the configuration automatically.
    local PG_HBA_CONF
    PG_HBA_CONF=$(find_pg_hba_conf)
    if [ -z "$PG_HBA_CONF" ]; then
        echo "Could not locate pg_hba.conf. Skipping authentication configuration..."
        echo "PostgreSQL must be manually configured to use md5 (or scram-sha-256) authentication for local connections."
        echo ""
    else
        echo "Checking if pg_hba.conf needs modification at $PG_HBA_CONF..."

        local needs_change=false
        # Check if explicit postgres peer rule is missing
        if ! sudo grep -qE '^[[:space:]]*local[[:space:]]+(all|postgres)[[:space:]]+postgres[[:space:]]+(peer|ident)' "$PG_HBA_CONF"; then
            needs_change=true
        fi
        
        # Check if generic local/loopback rules still use peer/ident/trust
        if sudo grep -qE '^[[:space:]]*local[[:space:]]+all[[:space:]]+all[[:space:]]+(peer|ident|trust)[[:space:]]*$' "$PG_HBA_CONF"; then
            needs_change=true
        fi
        if sudo grep -qE '^[[:space:]]*host[[:space:]]+all[[:space:]]+all[[:space:]]+(127\.0\.0\.1/32|::1/128)[[:space:]]+(peer|ident|trust)[[:space:]]*$' "$PG_HBA_CONF"; then
            needs_change=true
        fi

        if $needs_change; then
            echo "Modifying $PG_HBA_CONF for secure authentication..."
            sudo cp -n "$PG_HBA_CONF" "${PG_HBA_CONF}.bak" >/dev/null 2>&1 || true

            # Inject the postgres peer rule at the top of the file if missing
            if ! sudo grep -qE '^[[:space:]]*local[[:space:]]+(all|postgres)[[:space:]]+postgres[[:space:]]+(peer|ident)' "$PG_HBA_CONF"; then
                sudo sed -i '1i local   all             postgres                                peer' "$PG_HBA_CONF" \
                    || { echo "Modification of $PG_HBA_CONF failed"; exit 1; }
            fi

            # Switch local and loopback rules to md5
            sudo sed -i -E \
                -e 's/^([[:space:]]*local[[:space:]]+all[[:space:]]+all[[:space:]]+)(peer|ident|trust)([[:space:]]*)$/\1md5\3/' \
                -e 's/^([[:space:]]*host[[:space:]]+all[[:space:]]+all[[:space:]]+(127\.0\.0\.1\/32|::1\/128)[[:space:]]+)(peer|ident|trust)([[:space:]]*)$/\1md5\4/' \
                "$PG_HBA_CONF" || { echo "Modification of $PG_HBA_CONF failed"; exit 1; }
                
            pg_hba_modified=true
        else
            echo "pg_hba.conf already configured properly, skipping..."
        fi
        echo ""
    fi

    if $pg_hba_modified; then
        echo "Restarting PostgreSQL service to apply authentication changes..."
        restart_postgresql_service
        # Bridge the post-restart readiness gap before any client psql call.
        wait_for_postgresql_ready
        echo ""
    fi

    # `sudo -u postgres` inherits the caller's cwd, which often produces a
    # `could not change directory to ...: Permission denied` warning when
    # the current directory is not readable by the postgres OS user (common
    # when the script is run from /root or a user-only directory). Switching
    # to /tmp avoids the noise without affecting any of the psql commands.
    cd /tmp

    # Step 2: ensure the target database exists.
    echo "Checking if '$DENARO_DATABASE_NAME' database exists..."
    local db_exists
    db_exists=$(sudo -u postgres psql -X -A -t -v dbname="$DENARO_DATABASE_NAME" \
        <<< "SELECT 1 FROM pg_database WHERE datname = :'dbname';")
    if [ "$db_exists" != "1" ]; then
        echo "Creating '$DENARO_DATABASE_NAME' database..."
        sudo -u postgres psql -c "CREATE DATABASE \"$DENARO_DATABASE_NAME\";" >/dev/null 2>&1 \
            || { echo "Database creation failed"; exit 1; }
        db_created=true
    else
        echo "'$DENARO_DATABASE_NAME' database already exists, skipping..."
    fi
    echo ""

    # Step 3: ensure the database role (user) exists.
    echo "Checking if the database user exists..."
    local role_exists
    role_exists=$(sudo -u postgres psql -X -A -t -v role="$POSTGRES_USER" \
        <<< "SELECT 1 FROM pg_roles WHERE rolname = :'role';")
    if [ "$role_exists" != "1" ]; then
        echo "Creating user $POSTGRES_USER..."
        sudo -u postgres psql -c "CREATE USER \"$POSTGRES_USER\";" >/dev/null 2>&1 \
            || { echo "User creation failed"; exit 1; }
    else
        echo "Database user '$POSTGRES_USER' already exists, skipping..."
    fi
    echo ""

    # Step 4: ensure the role has a password set, and re-set it if the user
    # changed POSTGRES_PASSWORD during the .env configuration step.
    #
    # The `rolpassword IS NULL` SELECT distinguishes two states:
    #   "f" - rolpassword is NOT NULL: a password is already set.
    #   "t" - rolpassword IS NULL: no password set yet.
    # An empty result (newly-created role on some versions) is treated as
    # "needs password" since it is not "f". The password itself is supplied
    # via psql's -v substitution (:'pw'), which performs SQL literal
    # quoting safely regardless of the password's contents.
    echo "Checking if password is set for database user..."
    local has_password
    has_password=$(sudo -u postgres psql -X -A -t -v role="$POSTGRES_USER" \
        <<< "SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = :'role';")
    if [ "$has_password" != "f" ]; then
        echo "Setting password for database user..."
        sudo -u postgres psql -X -v pw="$POSTGRES_PASSWORD" \
            <<< "ALTER USER \"$POSTGRES_USER\" WITH PASSWORD :'pw';" >/dev/null 2>&1 \
            || { echo "Setting password failed"; exit 1; }
        echo "Password set."
    else
        if $db_pass_changed; then
            echo "Rotating password for database user..."
            sudo -u postgres psql -X -v pw="$POSTGRES_PASSWORD" \
                <<< "ALTER USER \"$POSTGRES_USER\" WITH PASSWORD :'pw';" >/dev/null 2>&1 \
                || { echo "Setting password failed"; exit 1; }
            echo "Password set."
        else
            echo "Password already set for database user, skipping..."
        fi
    fi
    echo ""

    # Step 5: grant the user CONNECT, CREATE, and TEMPORARY privileges on the
    # database, plus full privileges on the public schema, tables, and sequences.
    #
    # The comprehensive check validates that the user holds ALL required database
    # rights, schema rights, AND explicitly checks the CRUD privileges on every
    # existing table and sequence. (Note: PostgreSQL's has_*_privilege functions
    # do not accept 'ALL' as a parameter, and comma-separated lists evaluate as 'OR',
    # so each privilege must be checked individually with AND).
    echo "Checking if user has all required database, schema, and table privileges..."
    local priv_query="
SELECT (
  has_database_privilege(:'role', :'dbname', 'CONNECT')
  AND has_database_privilege(:'role', :'dbname', 'CREATE')
  AND has_database_privilege(:'role', :'dbname', 'TEMPORARY')
  AND has_schema_privilege(:'role', 'public', 'USAGE')
  AND has_schema_privilege(:'role', 'public', 'CREATE')
  AND NOT EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND NOT (
        has_table_privilege(:'role', c.oid, 'SELECT')
        AND has_table_privilege(:'role', c.oid, 'INSERT')
        AND has_table_privilege(:'role', c.oid, 'UPDATE')
        AND has_table_privilege(:'role', c.oid, 'DELETE')
      )
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'S'
      AND NOT (
        has_sequence_privilege(:'role', c.oid, 'USAGE')
        AND has_sequence_privilege(:'role', c.oid, 'UPDATE')
        AND has_sequence_privilege(:'role', c.oid, 'SELECT')
      )
  )
);"
    local has_all_privs
    has_all_privs=$(sudo -u postgres psql -X -A -t \
        -d "$DENARO_DATABASE_NAME" \
        -v role="$POSTGRES_USER" -v dbname="$DENARO_DATABASE_NAME" \
        <<< "$priv_query")

    if [ "$has_all_privs" != "t" ]; then
        echo "Granting required privileges to user '$POSTGRES_USER'..."
        sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE \"$DENARO_DATABASE_NAME\" TO \"$POSTGRES_USER\";" >/dev/null 2>&1 \
            || { echo "Granting database privileges failed"; exit 1; }
        sudo -u postgres psql -d "$DENARO_DATABASE_NAME" \
            -c "GRANT ALL ON SCHEMA public TO \"$POSTGRES_USER\";" >/dev/null 2>&1 \
            || { echo "Granting schema privileges failed"; exit 1; }
        sudo -u postgres psql -d "$DENARO_DATABASE_NAME" \
            -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO \"$POSTGRES_USER\";" >/dev/null 2>&1 \
            || { echo "Granting table privileges failed"; exit 1; }
        sudo -u postgres psql -d "$DENARO_DATABASE_NAME" \
            -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO \"$POSTGRES_USER\";" >/dev/null 2>&1 \
            || { echo "Granting sequence privileges failed"; exit 1; }
        echo "Privileges granted."
    else
        echo "User already has all required privileges, skipping..."
    fi
    echo ""

    # Step 6: assign database ownership to our user.
    #
    # PostgreSQL 15+ tightened the public schema permissions; being the
    # database owner sidesteps the new restrictions and allows the schema
    # import below to succeed without further GRANTs.
    echo "Checking if database owner is already '$POSTGRES_USER'..."
    local current_owner
    current_owner=$(sudo -u postgres psql -X -A -t -v dbname="$DENARO_DATABASE_NAME" \
        <<< "SELECT pg_catalog.pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d WHERE d.datname = :'dbname';")
    if [ "$current_owner" != "$POSTGRES_USER" ]; then
        echo "Setting database owner to '$POSTGRES_USER'..."
        sudo -u postgres psql -c "ALTER DATABASE \"$DENARO_DATABASE_NAME\" OWNER TO \"$POSTGRES_USER\";" >/dev/null 2>&1 \
            || { echo "Setting database owner failed"; exit 1; }
        echo "Database owner set."
    else
        echo "Database owner is already '$POSTGRES_USER'."
    fi
    echo ""

    # Restore the original cwd before the schema import so the relative
    # path 'denaro/schema.sql' resolves correctly.
    cd "$original_dir"

    # Step 7: import the schema if a new database was created on this run.
    #
    # The schema import connects via Unix socket (no -h flag) and
    # authenticates with PGPASSWORD; the just-set md5 method on
    # `local all all` (or a previously-applied scram-sha-256) permits
    # this. The schema is idempotent (CREATE TABLE IF NOT EXISTS), but
    # tying the import to fresh database creation avoids spurious
    # re-imports on benign re-runs of the script.
    if $db_created; then
        echo "Importing database schema from denaro/schema.sql..."
        PGPASSWORD="$POSTGRES_PASSWORD" psql -X \
            -U "$POSTGRES_USER" \
            -d "$DENARO_DATABASE_NAME" \
            -c "SET client_min_messages TO WARNING;" \
            -f denaro/schema.sql >/dev/null 2>&1 \
            || { echo "Schema import failed"; exit 1; }
        echo ""
        echo "Database setup complete!"
        echo ""
    else
        echo "No new database created; schema import skipped."
        echo ""
    fi
}

# =============================================================================
# Main flow
# =============================================================================
# The script supports two top-level paths:
#   - SETUP_DB_ONLY: install required packages and configure the database,
#     then exit. Skips Python venv setup, dependency installation, and node
#     startup. Useful for setups where Python is managed by another tool.
#   - Default: full setup followed by an interactive prompt to start the node.
#
# Both paths begin with system package installation (unless suppressed by
# --skip-package-install), .env configuration, and database setup.
if $SETUP_DB_ONLY; then
    if $SKIP_PACKAGE_INSTALL; then
        # Detection still runs because setup_database needs PKG_MGR to choose
        # the correct PostgreSQL initialization path on the current distro.
        detect_package_manager
        echo "Skipping system package installation as requested."
    else
        update_and_install_packages
    fi
    set_env_variables
    setup_database
    exit 0
fi

if $SKIP_PACKAGE_INSTALL; then
    detect_package_manager
    echo "Skipping system package installation as requested."
else
    update_and_install_packages
fi

# Lock in the Python interpreter AFTER packages have been installed (or
# skipped). Every subsequent python / pip invocation uses $PYTHON_CMD.
echo ""
echo "Resolving Python interpreter..."
resolve_python
local_py_version=$( "$PYTHON_CMD" -c \
    'import sys; print("{}.{}.{}".format(*sys.version_info[:3]))' 2>/dev/null )
echo "Using Python: $PYTHON_CMD (${local_py_version:-unknown version})"
echo ""

set_env_variables
setup_database

echo "Checking if Python virtual environment exists..."


# =============================================================================
# Python virtual environment + dependency installation
# =============================================================================

# is_valid_venv: return success (0) when the venv directory contains the
# minimum structure required to activate and use it, failure (1) otherwise.
#
# A venv is considered valid when:
#   1. The activate script exists at $VENV_DIR/bin/activate.
#   2. A Python binary exists inside the venv (bin/python or bin/python3).
#   3. The Python binary can actually execute a trivial command.
#
# Partially-created or corrupted venvs (e.g. from a previous failed
# `python3 -m venv` that left an empty directory tree, or from upgrading
# the system Python without recreating the venv) fail one or more of these
# checks.
is_valid_venv() {
    local venv_path="$1"
    # Check 1: activate script exists.
    if [ ! -f "$venv_path/bin/activate" ]; then
        return 1
    fi
    # Check 2: a Python binary exists inside the venv.
    local venv_python=""
    if [ -x "$venv_path/bin/python" ]; then
        venv_python="$venv_path/bin/python"
    elif [ -x "$venv_path/bin/python3" ]; then
        venv_python="$venv_path/bin/python3"
    else
        return 1
    fi
    # Check 3: the Python binary can actually run.
    if ! "$venv_python" -c 'import sys' >/dev/null 2>&1; then
        return 1
    fi
    return 0
}


# create_venv: create a fresh virtual environment and activate it.
# On failure, any partially-created directory is removed so subsequent
# runs do not mistake a broken tree for a usable venv.
#
# Returns 0 on success, 1 on failure.
create_venv() {
    echo "Creating virtual environment in ./$VENV_DIR..."
    if ! $PYTHON_CMD -m venv "$VENV_DIR"; then
        echo ""
        echo "Virtual environment creation failed."
        # Clean up the partially-created directory so it is not mistaken
        # for a valid venv on the next run.
        if [ -d "$VENV_DIR" ]; then
            echo "Removing partially-created ./$VENV_DIR directory..."
            rm -rf "$VENV_DIR"
        fi
        return 1
    fi
    # Post-creation validation: the venv command can exit 0 but still
    # produce a broken tree (e.g. ensurepip fails on some distros).
    if ! is_valid_venv "$VENV_DIR"; then
        echo ""
        echo "Virtual environment was created but appears invalid (missing activate script or broken Python binary)."
        echo "Removing ./$VENV_DIR directory..."
        rm -rf "$VENV_DIR"
        return 1
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    echo "Virtual environment created and activated."
    return 0
}


# Create the project venv if missing, validate and activate an existing one,
# or offer to recreate a broken one.
# Honors SKIP_PROMPTS to suppress the interactive y/n questions.
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        echo "A virtual environment does not exist."
        echo ""
        if $SKIP_PROMPTS; then
            create_venv || { echo "Unable to create virtual environment."; exit 1; }
        else
            echo "Creating a Python virtual environment is highly recommended to avoid dependency conflicts with system-wide Python packages."
            echo "It provides an isolated space for project dependencies."
            local create_venv_choice
            prompt_yes_no "Do you want to create a Python virtual environment? (y/n):" create_venv_choice
            case "$create_venv_choice" in
                y)
                    echo ""
                    create_venv || { echo "Unable to create virtual environment."; exit 1; }
                    ;;
                n)
                    echo ""
                    echo "Skipped..."
                    ;;
            esac
        fi
    else
        # The directory exists; validate before attempting activation.
        if is_valid_venv "$VENV_DIR"; then
            activate_venv
        else
            echo "Virtual environment at ./$VENV_DIR exists but appears corrupted or incomplete."
            echo "(Missing activate script, broken Python binary, or failed health check.)"
            echo ""
            if $SKIP_PROMPTS; then
                echo "Removing broken virtual environment and recreating..."
                rm -rf "$VENV_DIR"
                create_venv || { echo "Unable to recreate virtual environment."; exit 1; }
            else
                local recreate_choice
                prompt_yes_no "Do you want to delete it and create a new one? (y/n):" recreate_choice
                case "$recreate_choice" in
                    y)
                        echo ""
                        echo "Removing ./$VENV_DIR..."
                        rm -rf "$VENV_DIR"
                        create_venv || { echo "Unable to recreate virtual environment."; exit 1; }
                        ;;
                    n)
                        echo ""
                        echo "Keeping existing virtual environment. Some operations may fail."
                        ;;
                esac
            fi
        fi
    fi
}


# Activate an existing venv. Skipped silently when one is already active
# (VIRTUAL_ENV is set by the venv's activate script).
activate_venv() {
    if [[ -z "$VIRTUAL_ENV" ]]; then
        echo "Virtual environment already exists but is not active."
        if $SKIP_PROMPTS; then
            echo ""
            echo "Activating virtual environment..."
            # shellcheck disable=SC1091
            source "$VENV_DIR/bin/activate"
        else
            local activate_choice
            prompt_yes_no "Do you want to activate it? (y/n):" activate_choice
            case "$activate_choice" in
                y)
                    # shellcheck disable=SC1091
                    source "$VENV_DIR/bin/activate"
                    echo ""
                    echo "Virtual environment activated."
                    ;;
                n)
                    echo ""
                    echo "Skipped..."
                    ;;
            esac
        fi
    else
        echo "Virtual environment already exists and is active."
    fi
}


# is_externally_managed_python: return success (0) if the resolved Python
# installation is marked PEP 668 externally-managed, failure (1) otherwise.
#
# PEP 668 places an `EXTERNALLY-MANAGED` marker file alongside the standard
# library when the Python install is owned by an OS package manager (Debian
# 12+, Ubuntu 23.04+, Fedora 38+, Arch, and others). When the marker is
# present, `pip install` outside a virtual environment is rejected by pip
# itself with an error directing the user to use a venv or pipx. Detecting
# this up front lets the script emit a clear, actionable message before
# attempting an install that is guaranteed to fail.
#
# The marker may live at <stdlib>/EXTERNALLY-MANAGED (the canonical PEP 668
# location, used by Debian-family distros) or at the parent of <stdlib>
# under some non-standard layouts. Both are checked.
is_externally_managed_python() {
    local stdlib parent
    stdlib=$($PYTHON_CMD -c "import sysconfig; print(sysconfig.get_path('stdlib'))" 2>/dev/null)
    [ -z "$stdlib" ] && return 1
    if [ -f "$stdlib/EXTERNALLY-MANAGED" ]; then
        return 0
    fi
    parent=$(dirname "$stdlib")
    [ -f "$parent/EXTERNALLY-MANAGED" ]
}


# Compute the set of missing requirements.txt entries, then install them
# with pip. Warns the user when running outside a venv.
#
# Probe strategy:
#   The probe utilizes a two-strategy approach using standard or widely
#   available libraries, ensuring it works reliably across various Linux
#   distributions (including those that strip vendored pip libraries like Debian).
#     - Strategy 1: Attempts to use `pkg_resources` (from setuptools) for
#       accurate package and version dependency resolution.
#     - Strategy 2: If `pkg_resources` is missing (e.g., in fresh Python 3.12+
#       venvs), it falls back to `importlib.metadata` (stdlib on Python 3.8+)
#       to verify package presence by module name.
#
#   The probe communicates outcomes via the $PYTHON_CMD exit code:
#     0 - success: stdout is the newline-separated list of missing names
#         (possibly empty, meaning "everything is satisfied").
#     2 - probe is unavailable: required imports failed. The bash side
#         falls back to running `pip install -r requirements.txt`
#         unconditionally (after user consent), letting pip itself decide
#         what needs to be installed.
#     3 - requirements.txt is missing. The bash side surfaces a clear error.
#
# PEP 668 handling:
#   When the user has declined the venv prompt and is about to install
#   into the system Python, is_externally_managed_python is consulted. If
#   the marker file is present, the script warns the user that system-wide
#   pip installs are blocked. It then explicitly asks if the user wants to
#   force the install using the `--break-system-packages` flag.
#
#   Crucially, to prevent accidental system corruption, the global install
#   warning and the PEP 668 override prompt are shown REGARDLESS of the
#   --skip-prompts flag. This ensures that non-interactive automation
#   cannot silently corrupt an operating system's global Python environment.
pip_install() {
    echo ""
    echo "Checking required Python packages..."

    local probe_output probe_status
    probe_output=$($PYTHON_CMD - <<'PYEOF'
import sys
import os
import re

if not os.path.isfile('requirements.txt'):
    sys.exit(3)

missing = []

# Clean up requirements lines (remove comments and whitespace)
with open('requirements.txt') as fh:
    lines = [line.split('#')[0].strip() for line in fh if line.split('#')[0].strip()]

# Strategy 1: pkg_resources (Accurate name and version checking)
try:
    import pkg_resources
    for line in lines:
        try:
            pkg_resources.require(line)
        except (pkg_resources.DistributionNotFound, pkg_resources.VersionConflict):
            m = re.match(r'^([A-Za-z0-9_\-\.]+)', line)
            if m and m.group(1) not in missing:
                missing.append(m.group(1))
        except Exception:
            pass
    for m in missing:
        print(m)
    sys.exit(0)
except ImportError:
    pass

# Strategy 2: importlib.metadata (Fallback name-only checking for Python 3.8+)
# Guaranteed to work on modern Python, avoids external dependencies completely.
try:
    from importlib.metadata import version, PackageNotFoundError
    for line in lines:
        m = re.match(r'^([A-Za-z0-9_\-\.]+)', line)
        if not m:
            continue
        pkg_name = m.group(1)
        try:
            version(pkg_name)
        except PackageNotFoundError:
            try:
                # Some packages replace dashes with underscores in their module name
                version(pkg_name.replace('-', '_'))
            except PackageNotFoundError:
                if pkg_name not in missing:
                    missing.append(pkg_name)
    for m in missing:
        print(m)
    sys.exit(0)
except ImportError:
    sys.exit(2)
PYEOF
)
    probe_status=$?

    local missing_packages=()
    local probe_failed=false

    case "$probe_status" in
        0)
            # shellcheck disable=SC2207
            readarray -t missing_packages < <(printf '%s' "$probe_output")
            ;;
        3)
            echo "Error: requirements.txt not found in the current directory."
            exit 1
            ;;
        *)
            probe_failed=true
            echo "Could not introspect installed packages from this Python; will defer"
            echo "to pip for full requirement resolution."
            ;;
    esac

    if ! $probe_failed; then
        if [ "${#missing_packages[@]}" -eq 0 ] || { [ "${#missing_packages[@]}" -eq 1 ] && [ -z "${missing_packages[0]}" ]; }; then
            echo "Required packages are already installed."
            return 0
        fi
        local pretty
        pretty=$(IFS=', '; echo "${missing_packages[*]}")
        echo ""
        echo "The following packages from requirements.txt are missing:"
        echo "${pretty}."
    fi

    # First confirmation: install the missing packages at all.
    if ! $SKIP_PROMPTS; then
        local install_req
        prompt_yes_no "Do you want to install the missing Python packages? (y/n):" install_req
        case "$install_req" in
            y) ;;
            n) echo ""; echo "Cancelled..."; exit 0 ;;
        esac
    fi

    # Second confirmation: a global install (no venv) can corrupt system
    # Python packages on distros where pip writes into /usr/lib/python*. Warn
    # the user explicitly. These prompts are shown REGARDLESS of SKIP_PROMPTS
    # to ensure the user explicitly consents to a potentially destructive action.
    local pip_args=("-r" "requirements.txt")

    if [[ -z "$VIRTUAL_ENV" ]]; then
        echo ""
        echo "Warning: You are not currently in a virtual environment!"
        echo "Installing globally can affect system-wide Python packages and cause dependency conflicts."

        if is_externally_managed_python; then
            echo ""
            echo "Warning: This Python installation is marked as externally managed (PEP 668)."
            echo "System-wide pip install will be rejected by your distribution unless forced."
            
            local force_global
            prompt_yes_no "Do you want to force the installation using '--break-system-packages'? (y/n):" force_global
            case "$force_global" in
                y) pip_args=("--break-system-packages" "${pip_args[@]}") ;;
                n) echo ""; echo "Cancelled..."; exit 0 ;;
            esac
        else
            local confirm_global_install
            prompt_yes_no "Are you sure you want to continue? (y/n):" confirm_global_install
            case "$confirm_global_install" in
                y) ;;
                n) echo ""; echo "Cancelled..."; exit 0 ;;
            esac
        fi
    fi

    echo ""
    echo "Installing required Python packages..."
    echo ""
    
    # Run pip via the resolved interpreter's -m pip module to guarantee
    # we use the correct Python version's pip.
    $PYTHON_CMD -m pip install "${pip_args[@]}" || { echo "Failed to install python packages."; exit 1; }
    
    echo ""
    if [[ -z "$VIRTUAL_ENV" ]]; then
        echo "Python packages installed globally."
    else
        echo "Python packages installed within virtual environment."
    fi
}


setup_venv
pip_install


echo ""
echo "Node setup complete!"
echo ""
echo "Ready to start the Denaro node."


# =============================================================================
# Node startup
# =============================================================================
# Launch the Denaro node via run_node.py. Distinguishes a normal Ctrl+C
# (exit 130, SIGINT) from a real failure: the former is treated as a
# clean stop and reported as such. run_node.py reads DENARO_NODE_HOST and
# DENARO_NODE_PORT from .env via denaro.constants and starts uvicorn in
# the foreground.
start_node() {
    echo ""
    echo "Starting Denaro node on http://$DENARO_NODE_HOST:$DENARO_NODE_PORT..."
    echo "Press Ctrl+C to exit."
    echo ""
    $PYTHON_CMD run_node.py
    local rc=$?
    if [ $rc -eq 0 ] || [ $rc -eq 130 ]; then
        return 0
    fi
    echo "Failed to start Denaro Node (exit code $rc)"
    exit 1
}

if $SKIP_PROMPTS; then
    start_node
else
    start_choice=""
    prompt_yes_no "Do you want to start the Denaro node now? (y/n):" start_choice
    if [[ "$start_choice" == "y" ]]; then
        start_node
    else
        echo "Skipped..."
    fi
fi

echo ""
echo "Script executed successfully."