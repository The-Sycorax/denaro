# Denaro
[![Language](https://img.shields.io/badge/Language-Python%203.8+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Linux%20or%20WSL2-brightgreen.svg)]()
[![License: AGPLv3](https://img.shields.io/badge/License-AGPLv3-yellow.svg)](https://opensource.org/license/agpl-v3)

**Denaro**, "money" in Italian, is a decentralized cryptocurrency built entirely in Python and utilizes PostgreSQL for blockchain data. It offers a blockchain implementation that developers can understand and extend without the complexity often found in traditional cryptocurrency codebases. Additionally, it can serve as a foundation for developers that are interested in creating their own cryptocurrency.

<details>
<summary><b>Features:</b></summary>
<dl><dd>

* Proof-of-Work blockchain using SHA256 hashing with dynamic difficulty adjustment every 512 blocks. Blocks are limited to 2MB and can process approximately 3,800 transactions.
  
* Peer-to-peer network with cryptographic node identity, ECDSA-based request signing, and automatic blockchain synchronization. Includes reputation management, rate limiting, and security measures for network protection.
  
* Transaction system supporting up to 6 decimal places with ECDSA signature verification. Transactions can include up to 255 inputs and outputs, with optimized signature schemes and optional messages.
  
* PostgreSQL database backend with indexed queries, connection pooling, and integrated transaction validation for efficient blockchain storage and retrieval.
  
* Consensus versioning system enabling clean protocol upgrades with support for both soft and hard forks through activation height scheduling.
  
* RESTful API interface built on FastAPI providing comprehensive blockchain interaction, transaction submission, and network queries with background task processing and CORS support.

</details>
</dl></dd>

<details>
<summary><b>Monetary Policy:</b></summary>
<dl><dd>
  
  **Denaro's monetary policy has been chosen for its optimal balance of a scarce total supply, frequent halving events, and long-term emission lifespan.**
  
  * Initial Reward Per Block: **64 DNR**
  * Halving Interval: **262,144 blocks**.
    * Targets ~2.5 years per halving.
  * Maximum halvings: **64**
  * Estimated Emission Lifespan: **~160 years**.
  * Maximum Total Supply: **33,554,432 DNR**

</details>
</dl></dd>

---

<img src="https://node.denaro.network/height.png?t=3" alt="Denaro's Current Block Height" width="150">

### Denaro Projects:
* **[The-Sycorax](https://github.com/The-Sycorax) / [Denaro Wallet Client GUI](https://github.com/The-Sycorax/DenaroWalletClient-GUI)**
* **[StellarisChain](https://github.com/StellarisChain) / [Quasar - Wallet Browser Extension](https://github.com/StellarisChain/quasar)**
* **[connor33341](https://github.com/connor33341) / [Denaro Pool](https://github.com/connor33341/denaro-pool)**
* **[connor33341](https://github.com/connor33341) / [DenaroCudaMiner (Solo+Pool)](https://github.com/connor33341/denarocudaminer)**
* **[Gamer000gaming](https://github.com/Gamer000gaming) / [denaro-faucet](https://github.com/Gamer000gaming/denaro-faucet)**
* **[witer33](https://github.com/witer33) / [DenaroCudaMiner (Solo)](https://github.com/witer33/denarocudaminer)**
* **[1460293896](https://github.com/1460293896) / [Denaro CUDA Miner (Pool)](https://github.com/1460293896/denaro-cuda-miner)**
* **[geiccobs](https://github.com/geiccobs) / [Denaro Solo Miner (CPU)](https://github.com/geiccobs/denaro-solo-miner)**
* **[geiccobs](https://github.com/geiccobs) / [Denaro Pool Miner (CPU)](https://github.com/geiccobs/denaro-pool-miner)**
* **[geiccobs](https://github.com/geiccobs) / [Denaro WASM Miner](https://github.com/geiccobs/denaro-wasm-miner)**
* **[denaro-coin](https://github.com/denaro-coin) / [DVM (Denaro Virtual Machine)](https://github.com/denaro-coin/dvm)**
* **[The-Sycorax](https://github.com/The-Sycorax) / [Denaro Vanity Generator](https://github.com/The-Sycorax/Denaro-Vanity-Generator)**
* **[Avecci-Claussen](https://github.com/Avecci-Claussen) / [Denaro-Vanity-Gen](https://github.com/Avecci-Claussen/Denaro-Vanity-Gen)**

### Links:
* **New Website: [https://denaro.mine.bz](https://denaro.mine.bz/)**
* **Denaro Nodes:**
  * **[https://node.denaro.network](https://node.denaro.network/)**
  * **[https://node-2.denaro.network](https://node-2.denaro.network/)**
  * **[https://denaro.dasnode.xyz]( https://denaro.dasnode.xyz/)**
  * **[https://denaro-node.aldgram-solutions.fr](https://denaro-node.aldgram-solutions.fr/)**

* **Blockchain Explorers:**
  * **[https://denaro-revival.netlify.app](https://denaro-revival.netlify.app/)**
  * **[https://denaro-explorer.aldgram-solutions.fr](https://denaro-explorer.aldgram-solutions.fr/)**
* **Discord Server: [https://discord.gg/4Sq2dK4KMv](https://discord.gg/4Sq2dK4KMv)**

---

## Startup Guide

<dl><dd>

### Node Setup:

<dl><dd>

**Configuration and deployment of a Denaro node can be achieved using one of three methods: the `setup.sh` script, `Docker`, or a manual setup. The `setup.sh` script and `Docker` automate the installation of all prerequisites and configure the node according to user preference, while the manual setup allows full control over each step of the setup process.**

**It is highly recommended to review the [Environment Configuration](#environment-configuration) section before setting up a Denaro node.**

<a id="setup-via-setup.sh"></a>
<details>
<summary><b>Setup via setup.sh:</b></summary>

<dl><dd>

The `setup.sh` script automates the configuration and deployment of a single Denaro node. It handles system package installation, environment configuration, PostgreSQL database setup, Python virtual environment creation, dependency installation, and node startup.

**Prerequisites:** A Linux distribution (or WSL2), `git`, and user with `sudo` privileges.

*Refer to the [System Package Installation](#system-package-installation) subsection below for the list of supported package managers and required system packages.*

**Commands:**

<dl><dd>

```bash
# Clone the Denaro repository to your local machine.
git clone https://github.com/The-Sycorax/denaro.git

# Change directory to the cloned repository.
cd denaro

# Make the setup script executable.
chmod +x setup.sh

# Execute the setup script with optional arguments if needed.
./setup.sh [--skip-prompts] [--setup-db] [--skip-package-install] [--package-manager <name>]
```

</dd></dl>

<details>
<summary><b>CLI Arguments:</b></summary>

<dl><dd>

- `--skip-prompts`: Runs the setup script non-interactively, bypasses all interactive prompts, and uses default values for all configuration variables.

- `--setup-db`: Limits the setup script's actions to system package installation and PostgreSQL database configuration. Skips Python virtual environment setup, dependency installation, and node startup.

- `--skip-package-install`: Skips the system package installation step. Should be used on distributions whose package manager is not natively supported by the script.

- `--package-manager <name>`: Overrides the auto-detected package manager. Accepts `apt`, `dnf`, `pacman`, or `zypper`.

</dd></dl>
</details>

<a id="system-package-installation"></a>
<details>
<summary><b>System Package Installation:</b></summary>

<dl><dd>

The setup script automatically detects the system's package manager and installs all required system packages. This can be overridden via the `--package-manager` argument. 

The following package managers are supported:
- `apt` (Debian, Ubuntu, and derivatives)
- `dnf` (Fedora, RHEL 8+, Rocky Linux, AlmaLinux)
- `pacman` (Arch Linux, Manjaro)
- `zypper` (openSUSE, SUSE Linux Enterprise)

For distributions that use a different package manager, the required system packages must be installed manually before running the script with the `--skip-package-install` argument. 

Additionally, it is nessessary to ensure that the package names specified are adjusted to correspond with those recognized by your package manager, and that Python version 3.8 or higher is installed.

**Required Packages:**

- **Debian / Ubuntu (`apt`):** `gcc`, `libgmp-dev`, `libpq-dev`, `postgresql`, `python3`, `python3-dev`, `python3-venv`
- **Fedora / RHEL (`dnf`):** `gcc`, `gmp-devel`, `libpq-devel`, `postgresql-server`, `python3`, `python3-devel`
- **Arch Linux (`pacman`):** `gcc`, `gmp`, `postgresql-libs`, `postgresql`, `python`
- **openSUSE (`zypper`):** `gcc`, `gmp-devel`, `postgresql-devel`, `postgresql-server`, `python3`, `python3-devel`

</dd></dl>
</details>

</dd></dl>
</details>

<details>
<summary><b>Setup via Docker:</b></summary>

<dl><dd>

The Docker setup provides a containerized deployment option for Denaro nodes. Unlike the `setup.sh` script, it encapsulates everything needed to run a Denaro node in isolated Docker containers. This avoids installing dependencies on the host system and prevents conflicts with system packages. Additionally, the Docker setup allows for multi-node deployments, while the `setup.sh` script does not.

At the core of the Docker setup is the `docker-entrypoint.sh` script, which automates the configuration and deployment of each node. When a node's container starts, this script automatically provisions the PostgreSQL database, generates the necessary environment configuration, handles bootstrap node selection, and starts the Denaro node. Docker coordinates the supporting services, shared resources, and startup order of each container.

To test public node behavior over the Internet, the Docker setup includes optional support for exposing a node on the Internet by establishing an SSH reverse tunnel via [Pinggy.io's free tunnleing service](https://www.pinggy.io). *For more information please refer to: [2025-09-18-refactor(docker).md: Optional Public Node Tunnleing](https://github.com/The-Sycorax/denaro/blob/main/changelogs/2025/09/2025-09-18-refactor(docker).md#optional-public-node-tunnleing)*.

**Prerequisites:** A Linux distribution (or WSL2), `git`, `Docker` and the `Docker Compose` plugin installed, and a user with permissions to run `docker` commands.

**Commands:**

<dl><dd>

```bash
# Clone the Denaro repository to your local machine.
git clone https://github.com/The-Sycorax/denaro.git

# Change directory to the cloned repository.
cd denaro

# Create the PostgreSQL volume
docker volume create denaro_postgres_volume

# Run the Docker containers
docker compose -f ./docker/docker-compose.yml up --build --force-recreate -d

# Optionally show node logs
docker logs -f <container-name>
```

</dd></dl>

<details>
<summary><b>Custom Node Configuration:</b></summary>

<dl><dd>

***For documentation related to Denaro's Docker setup, please refer to: [2025-09-18-refactor(docker).md](https://github.com/The-Sycorax/denaro/blob/main/changelogs/2025/09/2025-09-18-refactor(docker).md) and [2025-10-14-refactor(docker).md](https://github.com/The-Sycorax/denaro/blob/main/changelogs/2025/10/2025-10-14-refactor(docker).md). Please note that some information may be outdated.***

To add or modify nodes in `docker-compose.yml`, use the structure outlined in the examples below.

<details>
<summary><b>Basic Node Example (Default):</b></summary>

<dl><dd>

```yaml
  denaro-node-3006:
    <<: *denaro-node-base
    image: denaro-node-3006
    container_name: denaro-node-3006
    hostname: denaro-node-3006
    volumes:
      - denaro_node_3006_volume:/app
      - denaro_node_registry_volume:/shared/denaro-node-registry
      - denaro_node_topology_volume:/shared/denaro-node-topology:ro
    depends_on:
      denaro-node-topology: { condition: service_completed_successfully }
      postgres: { condition: service_started }
    ports: [ "3006:3006" ]
    environment:
      NODE_NAME: 'denaro-node-3006'
      DENARO_NODE_PORT: '3006'
      DENARO_BOOTSTRAP_NODE: 'https://node.denaro.network'
      #ENABLE_PINGGY_TUNNEL: 'true'
      #DENARO_SELF_URL: ''

# Ensure the node's volume is added
volumes:
  denaro_node_3006_volume:
    name: denaro_node_3006_volume
```

</dd></dl>
</details>

<details>
<summary><b>Multi-Node Example:</b></summary>

<dl><dd>

```yaml
  # First Node
  denaro-node-3006:
    <<: *denaro-node-base
    image: denaro-node-3006
    container_name: denaro-node-3006
    hostname: denaro-node-3006
    volumes:
      - denaro_node_3006_volume:/app
      - denaro_node_registry_volume:/shared/denaro-node-registry
      - denaro_node_topology_volume:/shared/denaro-node-topology:ro
    depends_on:
      denaro-node-topology: { condition: service_completed_successfully }
      postgres: { condition: service_started }
    ports: [ "3006:3006" ]
    environment:
      NODE_NAME: 'denaro-node-3006'
      DENARO_NODE_PORT: '3006'
      DENARO_BOOTSTRAP_NODE: 'https://node.denaro.network'
      #ENABLE_PINGGY_TUNNEL: 'true'
      #DENARO_SELF_URL: ''

  # Second Node
  denaro-node-3007:
    <<: *denaro-node-base
    image: denaro-node-3007
    container_name: denaro-node-3007
    hostname: denaro-node-3007
    volumes:
      - denaro_node_3007_volume:/app
      - denaro_node_registry_volume:/shared/denaro-node-registry
      - denaro_node_topology_volume:/shared/denaro-node-topology:ro
    depends_on:
      denaro-node-topology: { condition: service_completed_successfully }
      postgres: { condition: service_started }

      # This condition is meant for proper startup ordering, but is really only
      # nessessary if the DENARO_BOOTSTRAP_NODE variable is set to a node that
      # is already present in the compose file.
      denaro-node-3006: { condition: service_healthy }

    ports: [ "3007:3007" ]
    environment:
      NODE_NAME: 'denaro-node-3007'
      DENARO_NODE_PORT: '3007'
      DENARO_BOOTSTRAP_NODE: 'http://denaro-node-3006:3006'  # Connects to first node

  # Third Node
  denaro-node-3008:
    <<: *denaro-node-base
    image: denaro-node-3008
    container_name: denaro-node-3008
    hostname: denaro-node-3008
    volumes:
      - denaro_node_3008_volume:/app
      - denaro_node_registry_volume:/shared/denaro-node-registry
      - denaro_node_topology_volume:/shared/denaro-node-topology:ro
    depends_on:
      denaro-node-topology: { condition: service_completed_successfully }
      postgres: { condition: service_started }

      # This condition is meant for proper startup ordering, but is really only
      # nessessary if the DENARO_BOOTSTRAP_NODE variable is set to a node that
      # is already present in the compose file.
      denaro-node-3007: { condition: service_healthy }

    ports: [ "3008:3008" ]
    environment:
      NODE_NAME: 'denaro-node-3008'
      DENARO_NODE_PORT: '3008'
      DENARO_BOOTSTRAP_NODE: 'http://denaro-node-3007:3007' # Connects to second node

# Ensure that the volumes of additional nodes are added
volumes:
  denaro_node_3006_volume:
    name: denaro_node_3006_volume
  denaro_node_3007_volume:
    name: denaro_node_3007_volume
  denaro_node_3008_volume:
    name: denaro_node_3007_volume
```

</dd></dl>
</details>

<details>
<summary><b>Important Notes:</b></summary>

<dl><dd>

**This information is meant to document the correct requirements for the Docker setup. This applies primarily to advanced setups and custom configurations. *The default docker-compose.yml and examples above already satisfy these requirements.***

- Each node service must include the `<<: *denaro-node-base` merge. This ensures that Docker Compose applies the required `denaro.node=true` label, mounts the shared volumes, and establishes the baseline dependencies on services that are required by the entrypoint script.

- Each node service requires its own dedicated volume (for example, `denaro_node_3006_volume`) mounted to `/app`. This volume preserves the node's configuration files, and application state across container restarts. Additionally, this volume should not be shared with other nodes, doing so may result in unexpected behavior.

- Each node service must be assigned a unique `NODE_NAME` and `DENARO_NODE_PORT` value. The entrypoint script uses these values to derive per-node database names and healthcheck targets. Duplicate values will cause database conflicts and prevent proper node identification.

- The shared `node-registry` and `node-topology` volumes must remain mounted on all node services. These volumes enable the entrypoint script to coordinate peer discovery through the shared registry and provide the dependency information required by the topology-aware healthcheck system.

- When configuring multi-node deployments, use `depends_on` with the `service_healthy` condition to establish startup ordering. This ensures that Docker Compose waits for upstream peer nodes to become healthy before launching dependent nodes, preventing bootstrap connection failures during startup.

</dd></dl>
</details>

</dd></dl>
</details>

</dd></dl>
</details>

<a id="manual-setup"></a>
<details>
<summary><b>Manual Setup:</b></summary>

<dl><dd>

A Denaro node can also be configured and deployed manually without the use of the `setup.sh` script or Docker. This is useful for users that require full control over the configuration process, or for systems where the automated setup script is not viable. 

The steps below replicate the operations performed by the `setup.sh` script, including system package installation, environment configuration, PostgreSQL database setup, Python virtual environment creation, and node startup.

**Prerequisites:** A Linux distribution (or WSL2), `git`, and user with `sudo` privileges.

<details>
<summary><b>1. Install Required System Packages:</b></summary>

<dl><dd>

*Refer to the [System Package Installation](#system-package-installation) subsection of [Setup via setup.sh](#setup-via-setup.sh) for the list of supported package managers and required system packages.*

</dd></dl>
</details>

<details>
<summary><b>2. Clone the Repository:</b></summary>

<dl><dd>

```bash
# Clone the Denaro repository to your local machine.
git clone https://github.com/The-Sycorax/denaro.git

# Change directory to the cloned repository.
cd denaro
```

</dd></dl>
</details>

<details>
<summary><b>3. Configure the <code>.env</code> File:</b></summary>

<dl><dd>

Create a `.env` file in the root of the Denaro directory and define the required environment variables. *For details on each variable please refer to the [Environment Configuration](#environment-configuration) section.*

**Example <code>.env</code> file ([.env.example](.env.example)):**

<dl><dd>

```bash
POSTGRES_USER='denaro'
POSTGRES_PASSWORD='denaro'
DENARO_DATABASE_NAME='denaro'
DENARO_DATABASE_HOST='127.0.0.1'

DENARO_NODE_HOST='127.0.0.1'
DENARO_NODE_PORT='3006'
DENARO_SELF_URL=''
DENARO_BOOTSTRAP_NODE='http://node.denaro.network'

LOG_LEVEL='INFO'
LOG_FORMAT='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
LOG_DATE_FORMAT='%Y-%m-%dT%H:%M:%S'
LOG_CONSOLE_HIGHLIGHTING='True'
LOG_INCLUDE_REQUEST_CONTENT='False'
LOG_INCLUDE_RESPONSE_CONTENT='False'
LOG_INCLUDE_BLOCK_SYNC_MESSAGES='False'
```

</dd></dl>

The default values shown above are sufficient for a local node. The values defined for `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `DENARO_DATABASE_NAME` must match those used during the PostgreSQL database setup in the next step.

</dd></dl>
</details>

<details>
<summary><b>4. Configure the PostgreSQL Database:</b></summary>

<dl><dd>

This step creates the database, creates the database user, sets the password, grants privileges, and assigns ownership. The values used in the commands below should match those defined in the `.env` file.

**Create the database, user, and grant privileges:**

<dl><dd>

```bash
# Create the Denaro database.
sudo -u postgres psql -c "CREATE DATABASE denaro;"

# Create the database user.
sudo -u postgres psql -c "CREATE USER denaro;"

# Set the password for the database user.
sudo -u postgres psql -c "ALTER USER denaro WITH PASSWORD 'denaro';"

# Grant privileges on the database to the user.
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE denaro TO denaro;"
sudo -u postgres psql -d denaro -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO denaro;"
sudo -u postgres psql -d denaro -c "GRANT ALL ON SCHEMA public TO denaro;"

# Assign ownership of the database to the user.
sudo -u postgres psql -c "ALTER DATABASE denaro OWNER TO denaro;"
```

</dd></dl>

**Enable password authentication:**

<dl><dd>

PostgreSQL must be configured to allow password (`md5`) authentication for local Unix domain socket connections. To do so the `pg_hba.conf` must be modified. This file is typically located at `/etc/postgresql/<PG_VERSION>/main/pg_hba.conf`, where `<PG_VERSION>` is the major version of the installed PostgreSQL package. 

Change the following line:

```
local   all             all                                     peer
```

To:

```
local   all             all                                     md5
```

Then restart PostgreSQL for the changes to take effect:

```bash
sudo service postgresql restart
```

</dd></dl>

**Import the database schema:**

<dl><dd>

```bash
# Import the Denaro database schema.
PGPASSWORD='denaro' psql -U denaro -d denaro -f denaro/schema.sql
```

*Replace the `PGPASSWORD` value, `-U` argument, and `-d` argument with those defined in the `.env` file.*

</dd></dl>

</dd></dl>
</details>

<details>
<summary><b>5. Create a Python Virtual Environment and Install Dependencies:</b></summary>

<dl><dd>

A Python virtual environment is highly recommended to avoid conflicts with system-wide Python packages.

```bash
# Create the virtual environment in ./venv.
python3 -m venv venv

# Activate the virtual environment.
source venv/bin/activate

# Install the required Python packages.
pip install -r requirements.txt
```

To deactivate the virtual environment at any time, run `deactivate`.

</dd></dl>
</details>

<details>
<summary><b>6. Start the Node:</b></summary>

<dl><dd>

With all prerequisites configured, the Denaro node can be started:

```bash
# Start the Denaro node.
python3 run_node.py

# Manualy start the Denaro node via uvicorn (Optional).
uvicorn denaro.node.main:app --host 127.0.0.1 --port 3006 
```

The node binds to the host and port defined by `DENARO_NODE_HOST` and `DENARO_NODE_PORT` in the `.env` file. To stop the node, press `Ctrl+C` in the terminal.

</dd></dl>
</details>

</dd></dl>
</details>

**When running a publically facing node, the node's own port (e.g. 3006) should be exposed to the Internet in order to allow connections to it.**


</dd></dl>
        
  ---

### Environment Configuration

<dl><dd>

Denaro uses environment variables for node and database configuration. In the standard setup without Docker, all environment vairables are managed through a `.env` file in Denaro's root directory. For the Docker Setup please refer to the [Docker Setup Configuration](#docker-setup-configuration) section. 

**All variable values should be enclosed in quotes.**

<details>
<summary><b>Environment Variables:</b></summary>

<dl><dd>

<details>
<summary><b>Node Configuration:</b></summary>

<dl><dd>

<details>
<summary><code>DENARO_NODE_HOST</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: The hostname or IP address the node binds to.

- **Required.**

- **Default**: `127.0.0.1`

</dd></dl>
</details>

<details>
<summary><code>DENARO_NODE_PORT</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: The port the node listens on for incoming connections.

- **Required.**

- **Default**: `3006`

</dd></dl>
</details>

<details>
<summary><code>NODE_NAME</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: A unique identifier for the node within the Docker Compose file. This variable is required since the entrypoint script uses its value to derive `DENARO_DATABASE_NAME`, as well as `DENARO_SELF_URL` when it is not explicitly set.

- Should match the container name of the node service.

- **Required; Docker only.**

</dd></dl>
</details>

<details>
<summary><code>DENARO_BOOTSTRAP_NODE</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: Specifies the bootstrap-node to connect to. This can be any Denaro node that is reachable via the Internet or internal network. It is used for joining Denaro's P2P network (mainnet or isolated), and discovering additional peers.

- The accepted values are different based on the setup type:

  <dl><dd>

  <details>
  <summary><b>Standard Setup:</b></summary>

  - `<url>`: Fixed HTTP(S) address of a Denaro Node.

  </details>

  <details>
  <summary><b>Docker Setup:</b></summary>

  <dl><dd>

  In the Docker setup, this variable specifies either the selection criteria, a node name, or a fixed HTTP(S) address of the bootstrap-node.
  
  </dd></dl>

  - `'self'`: Bootstraps against the node's own address (`DENARO_SELF_URL`). The node starts with no peers and remains isolated unless others connect to it.

  - `<url>`: Fixed HTTP(S) address of a Denaro Node.

  - `<node-name>`: The `NODE_NAME` of another node in the compose file. The target node must be public (with `ENABLE_PINGGY_TUNNEL=true`). The entrypoint script waits up to 120s for the target to publish its URL to the registry. Falls back to `'self'` if unavailable.

  - `'random'`: Randomly selects a bootstrap-node from all other public nodes (with `ENABLE_PINGGY_TUNNEL=true`) in the compose file. The entrypoint script waits up to 120s for all public nodes to register before choosing. Falls back to `'self'` if no public nodes are available.

  </details>

  </dd></dl>

- **Optional.**

- **Default**: `https://node.denaro.network`

</dd></dl>
</details>

<details>
<summary><code>DENARO_SELF_URL</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: Specifies the HTTP(S) address of the node itself, reachable via the Internet or internal network. Other peers use this address to connect back to the node, and is required for publicly facing nodes.

- When left unset or is set to a local address, the node will operate as a private node.

<dl><dd>


<details>
<summary><b>Docker Setup:</b></summary>


- `DENARO_SELF_URL` should only be directly specified if the node is publicly facing. Otherwise, the entrypoint script will set it automatically in one of two ways:

  - If left unset, its value is derived from `NODE_NAME` and `DENARO_NODE_PORT` as `http://{NODE_NAME}:{DENARO_NODE_PORT}`.

  - If `ENABLE_PINGGY_TUNNEL='true'`, its value is overridden with the public URL assigned to the node by Pinggy.io.

</details>
</dd></dl>


- **Optional.**

- **Default**: `unset`

</dd></dl>
</details>

<details>
<summary><code>ENABLE_PINGGY_TUNNEL</code>:</summary>

<dl><dd>

- *&lt;str-bool&gt;*: Enables public tunneling via Pinggy.io for up to 60 minutes. This overrides `DENARO_SELF_URL` with the public URL assigned to the node by Pinggy. Useful for testing public node behavior over the Internet.

- **Optional; Docker only.**

- **Default**: `'false'`

</dd></dl>
</details>

</dd></dl>
</details>

<details>
<summary><b>Database Configuration:</b></summary>

<dl><dd>

<details>
<summary><code>POSTGRES_USER</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: The PostgreSQL username used to authenticate with the database.

- **Required.**

- **Default**: `'denaro'`

</dd></dl>
</details>

<details>
<summary><code>POSTGRES_PASSWORD</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: The password for the PostgreSQL user.

- **Required.**

- **Default**: `'denaro'`

</dd></dl>
</details>

<details>
<summary><code>DENARO_DATABASE_NAME</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: The name of the node's PostgreSQL database.

<dl><dd>

<details>
<summary><b>Docker Setup:</b></summary>

- In the Docker setup, `DENARO_DATABASE_NAME` should not be directly specified. It is automatically set from `NODE_NAME` by the entrypoint script, with hyphens replaced by underscores.

</details>

</dd></dl>

- **Required for the standard setup, but not required for the Docker setup.**

- **Default**: `'denaro'`

</dd></dl>
</details>

<details>
<summary><code>DENARO_DATABASE_HOST</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: The hostname or IP address of the PostgreSQL server.

- **Required.**

- **Default**: `'127.0.0.1'`

</dd></dl>
</details>

<details>
<summary><code>DOCKER_ENABLE_PGADMIN</code>:</summary>

<dl><dd>

- *&lt;str-bool&gt;*: Toggles the pgAdmin container for browser-based database management.

- **Optional; Docker only.**

- **Default**: `'false'`

</dd></dl>
</details>

</dd></dl>
</details>

<details>
<summary><b>Logging Configuration:</b></summary>

<dl><dd>

<details>
<summary><code>LOG_LEVEL</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: Specifies the [logging verbosity level](https://docs.python.org/3/library/logging.html#levels) (e.g., `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).

- **Optional.**

- **Default**: `'INFO'`

</dd></dl>
</details>

<details>
<summary><code>LOG_FORMAT</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: Specifies the log message string using standard Python [logging format specifiers](https://docs.python.org/3/library/logging.html#logrecord-attributes).

- **Optional.**

- **Default**: `'%(asctime)s - %(levelname)s - %(name)s - %(message)s'`

</dd></dl>
</details>

<details>
<summary><code>LOG_DATE_FORMAT</code>:</summary>

<dl><dd>

- *&lt;str&gt;*: Specifies the date format for log timestamps using standard [strftime directives](https://strftime.org/).

- **Optional.**

- **Default**: `'%Y-%m-%dT%H:%M:%S'`

</dd></dl>
</details>

<details>
<summary><code>LOG_CONSOLE_HIGHLIGHTING</code>:</summary>

<dl><dd>

- *&lt;str-bool&gt;*: Toggles Rich console syntax highlighting for log outputs.

- **Optional.**

- **Default**: `'true'`

</dd></dl>
</details>

<details>
<summary><code>LOG_INCLUDE_REQUEST_CONTENT</code>:</summary>

<dl><dd>

- *&lt;str-bool&gt;*: Toggles HTTP request body content in the log.

- **Optional.**

- **Default**: `'false'`

</dd></dl>
</details>

<details>
<summary><code>LOG_INCLUDE_RESPONSE_CONTENT</code>:</summary>

<dl><dd>

- *&lt;str-bool&gt;*: Toggles HTTP response body content in the log.

- **Optional.**

- **Default**: `'false'`

</dd></dl>
</details>

<details>
<summary><code>LOG_INCLUDE_BLOCK_SYNC_MESSAGES</code>:</summary>

<dl><dd>

- *&lt;str-bool&gt;*: Toggles verbose blockchain synchronization messages in the log.

- **Optional.**

- **Default**: `'false'`

</dd></dl>
</details>

</dd></dl>
</details>

</dd></dl>
</details>

<a id="docker-setup-configuration"></a>
<details>
<summary><b>Docker Setup Configuration:</b></summary>

<dl><dd>

When deploying a Denaro node with Docker, configuration is split between a globally shared `.env` file in Denaro's root directory, and per-container in the `docker-compose.yml` file. This is to prevent variable overriding conflicts, especially for multi-node setups.

<details>
<summary><b>Global <code>.env</code> Variables:</b></summary>

<dl><dd>

These variables should only be included in the `.env` file, are shared across all containers, and are required for the Docker setup.

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `DENARO_DATABASE_HOST`
- `DENARO_NODE_HOST`
- `DOCKER_ENABLE_PGADMIN`

</dd></dl>
</details>

<details>
<summary><b>Per-Node Variables (<code>docker-compose.yml</code>):</b></summary>

<dl><dd>

These variables should be configured per-node within the `docker-compose.yml` environment block. They should **not** be included in the `.env` file.

- `NODE_NAME`
- `DENARO_NODE_PORT`
- `DENARO_BOOTSTRAP_NODE`
- `DENARO_SELF_URL`
- `ENABLE_PINGGY_TUNNEL`

</dd></dl>
</details>

<details>
<summary><b>Important Notes:</b></summary>

<dl><dd>

- `DENARO_DATABASE_NAME` should not be directly specified. It is automatically set from `NODE_NAME` by the entrypoint script, with hyphens replaced by underscores.

- `DENARO_SELF_URL` should only be directly specified if the node is publicly facing. Otherwise, the entrypoint script will set it automatically in one of two ways:

  - If left unset, its value is derived from `NODE_NAME` and `DENARO_NODE_PORT` as `http://{NODE_NAME}:{DENARO_NODE_PORT}`.

  - If `ENABLE_PINGGY_TUNNEL='true'`, its value is overridden with the public URL assigned to the node by Pinggy.io.

- Logging configuration variables can be set in either the `.env` file to apply globally, or per-node in the `docker-compose.yml` file.

</dd></dl>
</details>

</dd></dl>
</details>

</dd></dl>

---

### Database Management

<dl><dd>

The PostgreSQL database used by Denaro can be managed through several methods. 

> [!WARNING]
> Database management is intended for **development and testing purposes only** and should not be used in mainnet node environments.
>
> Additionally, it is highly recommended that the PostgreSQL port (5432) and pgAdmin port (5050) are not publicly exposed on the Internet, especially if default credentials are in use, since that would go against the most basic of security standards.

<details>
<summary><b>Management Options:</b></summary>

<dl><dd>

<details>
<summary><b>psql (CLI):</b></summary>

<dl><dd>

`psql` is the official PostgreSQL command-line client and provides direct access to the database. It can be used to run queries, inspect schemas, and perform administrative tasks.

<dl><dd>

```bash
# Connect to the Denaro database directly
PGPASSWORD='<POSTGRES_PASSWORD>' psql -h 127.0.0.1 -p 5432 -U <POSTGRES_USER> -d <DENARO_DATABASE_NAME>
```

*Replace `<POSTGRES_PASSWORD>`, and `<POSTGRES_USER>` with the credentials defined in the `.env` file.*

*Also replace `<DENARO_DATABASE_NAME>` with the value defined for it. This will be different based on the setup type:*
  - In the standard setup it's value is defined in the `.env` file.
  - In the Docker setup, it's value is automatically set from `NODE_NAME` by the entrypoint script, with hyphens replaced by underscores.

</dd></dl>

</dd></dl>
</details>

<details>
<summary><b>pgAdmin (Included in Docker Setup):</b></summary>

<dl><dd>

A [pgAdmin](https://www.pgadmin.org/) container is included in the Docker setup and provides a browser-based GUI for managing the PostgreSQL database. 

The pgAdmin container can be enabled by setting the `DOCKER_ENABLE_PGADMIN` variable to `true` in the `.env` file, and is accessible from the host machine once the Docker containers are running.

**Access:**

<dl><dd>

| Property | Value |
|----------|-------|
| URL | `http://localhost:5050` |
| Default Email | `admin@admin.com` |
| Default Password | `admin` |

</dd></dl>

> **Note:** If the default login credentials are in use, it is highly recommended that the pgAdmin port (`5050`) is not publicly exposed. The default login credentials for pgAdmin can be changed in it's envoronment block within the `docker-compose.yml` file. 

</dd></dl>
</details>

<details>
<summary><b>Third-Party GUI Clients:</b></summary>

<dl><dd>

Any PostgreSQL-compatible GUI client can be used to connect to the database. Popular options include:

- **[DBeaver](https://dbeaver.io/)** — A free, cross-platform database tool that supports PostgreSQL and many other databases.

- **[TablePlus](https://tableplus.com/)** — A modern, native GUI client for macOS, Windows, and Linux.

- **[DataGrip](https://www.jetbrains.com/datagrip/)** — A JetBrains IDE with advanced SQL editing and database management features.

To connect, use the nessessary values and credentials defined in the node's `.env` file. The database should generally be accessible at `127.0.0.1:5432` from the host machine. However, this address may be different if deployed using Docker.

</dd></dl>
</details>

</dd></dl>
</details>

</dd></dl>

</dd></dl>

---

## Running a Denaro Node

*Note: The information in this section is already documented in the [Manual Setup](#manual-setup) section, but is included here for easy reference. This section dose not apply to nodes deployed using Docker.*

A Denaro node can be started manually if you have already executed the `setup.sh` script and chose not to start the node immediately, or if you need to start the node in a new terminal session. 

It is recommended that a Python virtual environment is activated and that the required Python packages are installed prior to starting a node, particularly if the setup script was run with the `--setup-db` argument or if the installation was performed manually.

**Commands to manually start a node:**

<dl><dd>

```bash
# Navigate to the Denaro directory.
cd path/to/denaro

# Create a Python virtual environment (Optional).
sudo apt install python3-venv
python3 -m venv venv
source venv/bin/activate

# Install the required packages if needed.
pip install -r requirements.txt

# Start the Denaro Node
python3 run_node.py

# Manualy start the Denaro node via uvicorn (Optional).
uvicorn denaro.node.main:app --host 127.0.0.1 --port 3006 

# To stop the node, press Ctrl+C in the terminal.
```

</dl></dd>

**To exit a Python virtual environment:**

<dl><dd>

```bash
deactivate
```

</dl></dd>

---

## Nodeless Wallet Setup
To setup a nodeless wallet, use [Denaro Wallet Client GUI](https://github.com/The-Sycorax/DenaroWalletClient-GUI).

---

## Mining

**Denaro** adopts a Proof of Work (PoW) system for mining using SHA256 hashing, with dynamic difficulty adjustment every 512 blocks to maintain a target block time of 180 seconds (3 minutes).

<details>
<summary><b>Mining Details:</b></summary>

<dl><dd>

- **Block Hashing Algorithm**:
  - Utilizes the SHA256 algorithm for block hashing.
  - The hash of a block must begin with the last `difficulty` hexadecimal characters of the hash from the previously mined block.
  - `difficulty` can have decimal digits, which restricts the `difficulty + 1`st character of the derived hash to have a limited set of values.

    ```python
    from math import ceil

    difficulty = 6.0
    decimal = difficulty % 1

    charset = '0123456789abcdef'
    count = ceil(16 * (1 - decimal))
    allowed_characters = charset[:count]
    ```

- **Difficulty Adjustment**:
  - Difficulty adjusts every 512 blocks based on the actual block time versus the target block time of 180 seconds (3 minutes).
  - Starting difficulty is 6.0.

- **Block Size and Capacity**:
  - Maximum block size is 2MB (raw bytes), equivalent to 4MB in hexadecimal format.
  - Transaction data is limited to approximately 1.9MB hex characters per block.

- **Rewards**:
  - Block rewards start at 64 DNR and decrease by half every 262,144 blocks until they reach zero.

</dd></dl>
</details>

<details>
<summary><b>Mining Software:</b></summary>

<dl><dd>

- **CPU Mining**:

  The CPU miner script ([cpu_miner.py](miner/cpu_miner.py)) can be used to mine Denaro on any machine with Python version 3.8 or higher installed.
          
  <details>
  <summary><b>Usage:</b></summary>
  <dl><dd>
  
  - **Syntax**:
      ```bash
      python3 miner/cpu_miner.py [-h] [-a ADDRESS] [-n NODE] [-w WORKERS] [-m MAX_BLOCKS]
      ```
  
  - **Arguments**:
        
      * `--address`, `-a` (Required): Your public Denaro wallet address where mining rewards will be sent.

      * `--node`, `-n` (Optional): The URL or IP address of the Denaro node to connect to. Defaults to `http://127.0.0.1:3006/`.

      * `--workers`, `-w` (Optional): The number of parallel processes to run. It's recommended to set this to the number of CPU cores you want to use for mining. Defaults to 1.

      * `--max-blocks`, `-m` (Optional): Maximum number of blocks to mine before exiting. If not specified, the miner will continue indefinitely.

      * `--help`, `-h`: Shows the help message.

  <details>
  <summary><b>Examples:</b></summary>
  <dl><dd>
  
  - #### Basic Mining (Single Core)    
    ```bash
    python3 miner/cpu_miner.py --address WALLET_ADDRESS
    ```
  
  - #### Mining while connected to a Remote Node    
    ```bash
    python3 miner/cpu_miner.py --address WALLET_ADDRESS --node https://denaro.network
    ```
  
  - #### Mining with Multiple Cores    
    ```bash
    python3 miner/cpu_miner.py --address WALLET_ADDRESS --workers 8
    ```
  
  *(Replace `WALLET_ADDRESS` with your actual Denaro address)*
    
  </dd></dl>
  </dd></dl>
  </details>

- **GPU Mining**:
  
  The GPU mining script ([cuda_miner.py](miner/cuda_miner.py)) can be used to mine Denaro with a modern Nvidia GPU that supports CUDA.

  For more information on GPU mining please refer to [Denaro CUDA Miner Setup and Usage](https://github.com/The-Sycorax/denaro/tree/main/miner).

</dd></dl>
</details>

---

## Blockchain Synchronization

**Denaro** nodes maintain synchronization with the network through automatic peer discovery and chain validation mechanisms that ensure all nodes converge on the longest valid chain. Additionally nodes can also be manually synchronized.

<details>
<summary><b>Automatic Synchronization:</b></summary>

<dl><dd>

Nodes automatically detect and synchronize with longer chains through two mechanisms:

- **Handshake Synchronization**: When connecting to a peer, nodes exchange chain state information. If the peer has a longer valid chain, synchronization is triggered immediately.

- **Periodic Chain Discovery**: A background task polls 2 random peers every 60 seconds to check for longer chains, ensuring the node remains synchronized even without new connections.

</dd></dl>
</details>

<details>
<summary><b>Manual Synchronization:</b></summary>

<dl><dd>

To manually initiate blockchain synchronization, a request can be sent to a node's `/sync_blockchain` endpoint:

<dl><dd>

```bash
curl http://127.0.0.1:3006/sync_blockchain
```

</dl></dd>

<dl><dd>

The endpoint accepts an optional `node_id` parameter to sync from a specific peer. The node ID of a peer can be found in the `./data/active_peers.json` file, or via a node's `/get_peers` endpoint:

<dl><dd>

```bash
curl "http://127.0.0.1:3006/sync_blockchain?node_id=NODE_ID"
```

</dl></dd>
<dl><dd>
The endpoint returns an error if a sync operation is already in progress.

</dd></dl>
</details>

---

## License
Denaro is released under the terms of the GNU Affero General Public License v3.0. See [LICENSE](LICENSE) for more information or goto https://www.gnu.org/licenses/agpl-3.0.en.html

