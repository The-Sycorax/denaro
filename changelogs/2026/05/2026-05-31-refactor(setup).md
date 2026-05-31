**refactor(setup): add multi-distro support, improve .env and DB provisioning**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [bab10540001ab82d076490d95aa583ad0a7ba870](https://github.com/The-Sycorax/denaro/commit/bab10540001ab82d076490d95aa583ad0a7ba870)

**Date**: May 31st, 2026

---

## Overview:

This refactor includes a complete rewrite of `setup.sh`. The previous script was tightly coupled to Debian-based package installation via `apt` and lacked robustness around .env handling, Python version resolution, and database provisioning. This commit changes that and introduces multi-distribution package manager support, safe .env file parsing with proper quoting, a comprehensive PostgreSQL provisioning flow, intelligent Python version discovery, and PEP 668 awareness for global pip installs.

- The script now supports four package managers: `apt` (Debian/Ubuntu), `dnf` (Fedora/RHEL), `pacman` (Arch/Manjaro), and `zypper` (openSUSE/SUSE). Package manager detection is automatic with an optional `--package-manager <pm>` override for environments where multiple managers coexist.

- All .env reads and writes are routed through dedicated helper functions that handle special characters in values safely. Values are stored single-quoted with the standard POSIX `'\''` escape for embedded single quotes, eliminating the `eval`-based sourcing pitfalls of the previous implementation.

- PostgreSQL provisioning has been hardened with SQL identifier validation, psql variable substitution for passwords, idempotent role/database/privilege management, and distribution-aware service initialization. The `pg_hba.conf` configuration is now idempotent and recognises both `md5` and `scram-sha-256` as already-configured authentication methods.

- Python version resolution now probes versioned interpreters (`python3.13` down to `python3.8`) and selects the newest qualifying version. The corresponding distribution packages are resolved using a two-pass strategy that checks the local package database before querying repository metadata.

- A runtime requirement for Bash 4+ is enforced at script startup to prevent confusing failures from bash-4 features used throughout.

---

## CLI Arguments:

- **`--skip-prompts`**: Run non-interactively, using default values for all prompts.

- **`--setup-db`**: Configure system packages and PostgreSQL only. Skips Python venv setup, dependency installation, and node startup.

- **`--skip-package-install`**: Skip system package installation. Useful when packages are managed externally.

- **`--package-manager <pm>`**: Override auto-detection of the system package manager. Accepts one of: `apt`, `dnf`, `pacman`, `zypper`.

---

## New Functions:

- **Generic Helpers**:
  - **`prompt_yes_no`**:
    - Reads a y/n response from stdin, accepting `y`, `yes`, `n`, or `no` in any case. 
    
    - Re-prompts on invalid input. 
    
    - Sets the named variable to the canonical lowercase form (`y` or `n`). 
    
    - Replaces all ad-hoc `while true; read; case` input loops throughout the script.

  - **`validate_sql_identifier`**:
    - Returns success if the argument matches `[A-Za-z_][A-Za-z0-9_]*`. Used to gate all interpolation of `POSTGRES_USER` and `DENARO_DATABASE_NAME` into psql commands, preventing SQL injection.

  - **`is_local_db_host`**:
    - Returns success when the argument names the local machine (`""`, `127.0.0.1`, `::1`, `localhost`), failure otherwise. Used by `setup_database` to decide whether to provision a local PostgreSQL cluster or skip with guidance for remote databases.

  ---

- **.env File Helpers**:
  - **`write_env_var`**:
    - Persists `var_name='value'` into the target .env file, creating or replacing the existing line. 
    
    - Single quotes inside the value are escaped using the POSIX `'\''` idiom. 
    
    - Duplicate entries for the same variable are collapsed to a single canonical line. Safe for values containing slashes, ampersands, backslashes, single quotes, dollar signs, and whitespace.

  - **`dequote_env_value`**:
    - Inverse of `write_env_var`. Strips a single layer of single quotes and converts the `'\''` escape back to a literal single quote. 
    
    - Values not wrapped in single quotes are returned verbatim, supporting hand-edited .env files.

  ---

- **Package Manager Support**:

  - **`detect_package_manager`**:
    - Probes for `apt-get`, `dnf`, `pacman`, and `zypper` in descending estimated distribution share. 
    
    - When `PKG_MGR` is pre-set via `--package-manager`, validates that the binary exists on PATH. 
    
    - Replaces the previous hardcoded `apt`-only approach.

  - **`_python_pkgs_for_version`**:
    - Emits the complete set of Python packages required for a specific minor version on the detected package manager. Handles the naming conventions of each distro family (e.g., `python3.X-venv` on apt, `python3X-virtualenv` on zypper).

  - **`_is_pkg_available`**:
    - Returns success when the named package is available in the configured repositories. Used to validate every companion package before committing to a Python version.

  - **`determine_python_packages`**:
    - Selects the highest Python version (3.13 down to 3.8) where all required companion packages can be satisfied. 
    - Uses a two-pass strategy: 
      - 1: checks the local package database. 
        
      - 2: queries repository metadata. Ensures the full package set is satisfiable before committing to a version.

  - **`required_packages`**:
    - Returns the list of required system packages for the detected package manager, including or excluding Python packages based on whether `--setup-db` mode is active.

  - **`is_package_installed`**:
    - Returns success if the named package is installed. Uses `dpkg-query` for apt, `rpm -q` for dnf/zypper, and `pacman -Qi` for pacman.

  - **`update_package_lists`**:
    - Refreshes local package metadata. Only runs `sudo apt update` for apt. 
    
    - Intentionally omits `pacman -Sy` to avoid the partial-upgrade pitfall on Arch.

  - **`install_packages_via_pm`**:
    - Installs packages using the detected package manager with appropriate non-interactive flags when `--skip-prompts` is active.

  ---

- **Python Version Discovery**:

  - **`resolve_python`**:
    - Locates a Python interpreter on PATH that meets the minimum version requirement (3.8+). 
    
    - Probes versioned candidates (`python3.13` down to `python3.8`) followed by unversioned fallbacks (`python3`, `python`). 
    
    - Sets the global `PYTHON_CMD` used by all subsequent Python/pip invocations. 
    
    - Exits with a clear error if no qualifying interpreter is found.

  ---

- **PostgreSQL Service Helpers**:

  - **`manage_postgresql_service`**:
    - Runs a service command (start/restart) for the postgresql unit. Prefers `systemctl`, falls back to `service` for environments without systemd (e.g., WSL2).

  - **`initialize_postgres_service`**:
    - Initializes the PostgreSQL data directory and ensures the service is running. Distribution-aware: handles Debian's automatic initdb, Fedora's `postgresql-setup --initdb`, Arch's `sudo -iu postgres initdb`, and openSUSE's auto-init via systemd PreStart.

  - **`restart_postgresql_service`**:
    - Restarts PostgreSQL after `pg_hba.conf` modifications.

  - **`wait_for_postgresql_ready`**:
    - Waits for PostgreSQL to accept connections after restart. Prefers `pg_isready`, falls back to a superuser query. Bounded by a 30-second timeout to close the race window between restart and schema import.

  - **`find_pg_hba_conf`**:
    - Locates the active `pg_hba.conf` by querying the running cluster via `SHOW hba_file`, with fallback paths for Debian, RPM, and Arch distributions.

  ---

- **Virtual Environment**:

  - **`is_valid_venv`**:
    - Validates a venv directory by checking for the activate script, a Python binary, and that the binary can execute. Detects partially-created or corrupted venvs from failed creation attempts or system Python upgrades.

  - **`create_venv`**:
    - Creates a fresh virtual environment and activates it. Cleans up partially-created directories on failure. Includes post-creation validation.

  - **`is_externally_managed_python`**:
    - Detects PEP 668 externally-managed Python installations by checking for the `EXTERNALLY-MANAGED` marker file. Checks both the canonical location (`<stdlib>/EXTERNALLY-MANAGED`) and the parent directory for non-standard layouts.

  ---

## Modified Functions:

- **`update_variable`**:
  - Password input now uses masked entry with confirmation via `read_password_with_asterisks`. 
  
  - Port input now uses `validate_port_input`.
  
  - Now supports two modes: initial setup and update.
  
  - Non-interactive mode only fills in absent or blank .env entries, preserving existing values.
  
  - Now uses `write_env_var` instead of `sed`/`echo >>` for .env writes.
  
  - Includes a sanity guard that snaps invalid `DENARO_NODE_PORT` values to the default.
  
  - All `eval` usage replaced with `printf -v`.

- **`set_env_variables`**:
  - This function now handles three cases: complete .env (offer to update), incomplete .env (fill missing variables), and absent .env (create and populate). 
  
  - Password change detection now distinguishes between fresh installs (empty initial value) and actual rotations to avoid false positives. 
  
  - Uses `read_env_variable` with dequoting for snapshots instead of `grep | cut`.

- **`update_and_install_packages`**:
  - Top-level entry point for system package installation. Now detects the package manager, refreshes metadata, computes missing packages, and installs only what is needed. Includes a zypper-specific `libexpat1` update to avoid pyexpat symbol mismatches.

- **`read_password_with_asterisks`**:
  - Reads a password from stdin while echoing asterisks. Supports backspace editing. 
  
  - Saves and restores parent INT/EXIT traps to avoid stripping them during nested calls. 
  
  - Handles Ctrl+C gracefully by restoring tty settings.

- **`validate_port_input`**:
  - Validates user input as a TCP port number (1–65535). Falls back to the current .env value or script default on blank input. Re-prompts on invalid input. 
  
  - Includes defense against corrupted .env values.

- **`load_env_variables`**:
  - Parses the .env file line by line and sets corresponding shell globals for variables in the `ENV_VARS` list.
  
  - Comments and blank lines are skipped. 
  
  - Values are dequoted via `dequote_env_value`, avoiding the security pitfalls of `eval`-style sourcing. 
  
  - Empty values are intentionally not applied (preserving script defaults) except for `DENARO_SELF_URL` and `DENARO_BOOTSTRAP_NODE`, which are explicitly allowed to be blank.

- **`read_env_variable`**:
  - Returns the dequoted value of the named variable from the .env file. Used for change-detection snapshots during .env configuration.

- **`identify_missing_variables`**:
  - Emits a space-separated list of variables absent from the .env file or whose dequoted value is empty. Correctly classifies `VAR=''` and `VAR=` as missing, except for explicitly allowed variables.


- **`setup_database`**:
  - Completely rewritten with a seven-step idempotent provisioning flow:
    - 1: Configure `pg_hba.conf` with postgres peer rule injection and local/loopback md5 switching.
    
    - 2: Create the target database if it does not exist.
    
    - 3: Create the database role (user) if it does not exist.
    
    - 4: Set or rotate the role password based on change detection.
    
    - 5: Grant `CONNECT`, `CREATE`, `TEMPORARY`, `schema`, `table`, and `sequence` privileges with a comprehensive privilege check query.
    
    - 6: Assign database ownership (critical for PostgreSQL 15+ schema restrictions).
    
    - 7: Import schema only on fresh database creation.

  - Now validates SQL identifiers before any interpolation. Passes the role password via psql `-v` variable substitution. 
  
  - Detects remote `DENARO_DATABASE_HOST` and skips local provisioning with guidance. 
  
  - Warns when the user renames the role or database during .env configuration.

- **`setup_venv`**:
  - Updated to validate existing venvs via `is_valid_venv` and offer to recreate corrupted ones. Uses the resolved `$PYTHON_CMD` instead of bare `python3`.

- **`activate_venv`**:
  - Updated to use `prompt_yes_no` for input handling. Now uses `source "$VENV_DIR/bin/activate"` with proper quoting.

- **`pip_install`**:
  - Completely rewritten. Uses a two-strategy Python probe for missing package detection:
    - 1: `pkg_resources` for accurate name and version checking.
    - 2: `importlib.metadata` fallback for Python 3.8+ environments where setuptools is not available.
  - Handles PEP 668 externally-managed Python by offering `--break-system-packages`.
  - Global install warnings are now shown regardless of `--skip-prompts` to prevent silent system corruption. 
  
  - Now uses `$PYTHON_CMD -m pip` instead of bare `pip` to guarantee the correct Python version's pip is used.

- **`start_node`**:
  - Updated to use `$PYTHON_CMD` instead of `python3`. 
  
  - Now distinguishes a normal Ctrl+C (exit 130, SIGINT) from a real failure.

---

## Removed Functions:

- **`validate_start_node_response`**:
  - Replaced by a `prompt_yes_no` call at the script's end.

---

## Other Changes:

- **Bash 4+ Requirement**:
  - A runtime version check at the top of the script refuses to run under Bash 3, providing a clear error message instead of confusing "bad substitution" failures from bash-4 features.

- **CLI Argument Parsing**:
  - `--skip-package-install` replaces the previous `--skip-apt-install` flag name. The `--package-manager <pm>` argument is new, supporting `apt`, `dnf`, `pacman`, and `zypper`.

- **Default Configuration Values**:
  - `DENARO_BOOTSTRAP_NODE` default changed from `""` to `"https://node.denaro.network"`.
  - Added `LOG_LEVEL` (default: `"INFO"`) and `LOG_CONSOLE_HIGHLIGHTING` (default: `"True"`) to the managed variable list.
  
  - `VENV_DIR`, `PYTHON_CMD`, and minimum Python version constants are now defined as global variables.

- **Exit Code Handling**:
  - User cancellations (`n` responses to prompts) now exit with code `0` instead of `1`, correctly distinguishing voluntary cancellation from errors.
