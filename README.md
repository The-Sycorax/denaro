# Denaro
[![Language](https://img.shields.io/badge/language-Python%203.8+-blue.svg)](https://isocpp.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20or%20WSL2-brightgreen.svg)](https://www.microsoft.com/windows/)

**Denaro**, "money" in Italian, is a decentralized cryptocurrency built entirely in Python and utilizes PostgreSQL for blockchain data. It offers a blockchain implementation that developers can understand and extend without the complexity often found in traditional cryptocurrency codebases. Additionally, it can serve as a foundation for developers that are interested in creating their own cryptocurrency.

<details>
<summary><b>Features:</b></summary>
<dl><dd>

* Proof-of-Work blockchain using SHA256 hashing with dynamic difficulty adjustment every 512 blocks. Blocks are limited to 2MB and can process approximately 3,800 transactions (~21 transactions per second).
  
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

## Denaro Projects
* [Denaro Wallet Client GUI](https://github.com/The-Sycorax/DenaroWalletClient-GUI)
* [Denaro CUDA Pool miner](https://github.com/1460293896/denaro-cuda-miner)
* [DenaroCudaMiner (Solo)](https://github.com/witer33/denarocudaminer)
* [Denaro WASM Miner](https://github.com/geiccobs/denaro-wasm-miner)
* [Denaro CPU Pool Miner](https://github.com/geiccobs/denaro-pool-miner)
* [Denaro CPU Solo Miner](https://github.com/geiccobs/denaro-solo-miner)
* [DVM (Denaro Virtual Machine)](https://github.com/denaro-coin/dvm)
* [Denaro Vanity Generator](https://github.com/The-Sycorax/Denaro-Vanity-Generator)
* [Denaro-Vanity-Gen](https://github.com/Avecci-Claussen/Denaro-Vanity-Gen)

---

## Node Setup
**Automated configuration and deployment of a Denaro node are facilitated by the `setup.sh` script. It handles system package updates, manages environment variables, configures the PostgreSQL database, sets up a Python virtual environment, installs the required Python dependencies, and runs the Denaro node. This script ensures that all prerequisites for operating a Denaro node are met and properly configured according to the user's preference.**
 
- The setup script accepts three optional arguments to adjust its behavior during installation:

  - `--skip-prompts`: Executes the setup script in an automated manner without requiring user input, bypassing all interactive prompts.
  
  - `--setup-db`: Limits the setup script's actions to only configure the PostgreSQL database, excluding the execution of other operations such as virtual environment setup and dependency installation.

  - `--skip-package-install`: Skips `apt` package installation. This argument can be used for Linux distributions that do not utilize `apt` as a package manager. However, it is important that the required system packages are installed prior to running the setup script (For more details refer to: *Installation for Non-Debian Based Systems*).

**Execute the commands below to initiate the installation:**

  ```bash
  # Clone the Denaro repository to your local machine.
  git clone https://github.com/denaro-coin/denaro.git
  
  # Change directory to the cloned repository.
  cd denaro
  
  # Make the setup script executable.
  chmod +x setup.sh
  
  # Execute the setup script with optional arguments if needed.
  ./setup.sh [--skip-prompts] [--setup-db] [--skip-package-install]
  ```

<details>
<summary>Installation for Non-Debian Based Systems:</summary>

<dl><dd>
<dl><dd>

 The setup script is designed for Linux distributions that utilize `apt` as their package manager (e.g. Debian/Ubuntu). If system package installation is unsuccessful, it is most likely due to the absence of `apt` on your system. This is generally the case for Non-Debian Linux distributions. 
 
 Therefore, the required system packages must be installed manually. Below you will find a list of the required system packages.

<details>
<summary>Required Packages:</summary>
<dl><dd>

*Note: It is nessessary to ensure that the package names specified are adjusted to correspond with those recognized by your package manager.*

- `gcc`
- `libgmp-dev`
- `libpq-dev`
- `postgresql-15`
- `python3`
- `python3-venv`
- `sudo`
  
</dd></dl>
</details>

Once the required packages have been installed, the `--skip-package-install` argument can be used with the setup script to bypass operations which require `apt`. This should mitigate any unsucessful execution relating to package installation and allow the setup script to proceed.

</dd></dl>
</dd></dl>
</details>

**Setup with Docker**:

  ```bash
  cd path/to/denaro
  cd docker
  docker-compose -f docker-compose.yml up --build -d
  ```

---

## Running a Denaro Node

A Denaro node can be started manually if you have already executed the `setup.sh` script and chose not to start the node immediately, or if you need to start the node in a new terminal session. 

***Note:** For users who have used the setup script with the `--setup-db` argument or have performed a manual installation, it is reccomended that a Python virtual environment is set up and that the required Python packages are installed prior to starting a node.*

Execute the commands below to manually start a Denaro node:

```bash
# Navigate to the Denaro directory.
cd path/to/denaro

# Set up a Python virtual environment (Optional).
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

To exit a Python virtual environment, use the command:

```bash
deactivate
```

---

## Nodeless Wallet Setup
To setup a nodeless wallet, use [Denaro Wallet Client GUI](https://github.com/The-Sycorax/DenaroWalletClient-GUI).

---

## Mining

**Denaro** adopts a Proof of Work (PoW) system for mining using SHA256 hashing, with dynamic difficulty adjustment every 512 blocks to maintain a target block time of 180 seconds (3 minutes).

<details>
<summary>Mining Details:</summary>

<dl><dd>

- **Block Hashing**:
  - Utilizes the SHA256 algorithm for block hashing.
  - The hash of a block must begin with the last `difficulty` hexadecimal characters of the hash from the previously mined block.
  - `difficulty` can have decimal digits, which restricts the `difficulty + 1`st character of the derived hash to have a limited set of values.

    ```python
    from math import ceil

    difficulty = 6.3
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
<summary>Mining Software:</summary>

<dl><dd>

- **CPU Mining**:

  The CPU miner script (`./miner/cpu_miner.py`) can be used to mine Denaro.
          
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
    To mine using a single CPU core and the default local node:
    
    ```bash
    python3 miner/cpu_miner.py --address WALLET_ADDRESS
    ```
  
  - #### Mining with a Remote Node
  
    To mine while connected to a specific public node:
    
    ```bash
    python3 miner/cpu_miner.py --address WALLET_ADDRESS --node http://a-public-node.com:3006
    ```
  
  - #### Mining with Multiple Cores
  
    To mine using 8 CPU cores for higher performance:
    
    ```bash
    python3 miner/cpu_miner.py --address WALLET_ADDRESS --workers 8
    ```
  
  *(Replace `WALLET_ADDRESS` with your actual Denaro address)*
    
  </dd></dl>
  </dd></dl>
  </details>

- **GPU Mining**:

  For GPU mining please refer to [Denaro CUDA Miner Setup and Usage](https://github.com/The-Sycorax/denaro/tree/main/miner).

</dd></dl>
</details>

---

## Blockchain Synchronization

**Denaro** nodes maintain synchronization with the network through automatic peer discovery and chain validation mechanisms that ensure all nodes converge on the longest valid chain. Additionally nodes can also be manually synchronized.

<details>
<summary>Automatic Synchronization:</summary>

<dl><dd>

Nodes automatically detect and synchronize with longer chains through two mechanisms:

- **Handshake Synchronization**: When connecting to a peer, nodes exchange chain state information. If the peer has a longer valid chain, synchronization is triggered immediately.

- **Periodic Chain Discovery**: A background task polls 2 random peers every 60 seconds to check for longer chains, ensuring the node remains synchronized even without new connections.

</dd></dl>
</details>

<details>
<summary>Manual Synchronization:</summary>

<dl><dd>

To manually initiate blockchain synchronization, a request can be sent to a node's `/sync_blockchain` endpoint:

```bash
curl http://127.0.0.1:3006/sync_blockchain
```

The endpoint accepts an optional `node_id` parameter to sync from a specific peer. The node ID of a peer can be found in the `./denaro/node/nodes.json` file:

```bash
curl "http://127.0.0.1:3006/sync_blockchain?node_id=NODE_ID"
```

The endpoint returns an error if a sync operation is already in progress.

</dd></dl>
</details>

---

## License
Denaro is released under the terms of the GNU Affero General Public License v3.0. See [LICENSE](LICENSE) for more information or goto https://www.gnu.org/licenses/agpl-3.0.en.html

