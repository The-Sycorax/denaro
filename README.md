# Denaro
[![Language](https://img.shields.io/badge/language-Python%203.8+-blue.svg)](https://isocpp.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20or%20WSL2-brightgreen.svg)](https://www.microsoft.com/windows/)

**Denaro**, 'money' in Italian, is a cryptocurrency developed entirely in Python and utilizes PostgreSQL for it's blockchain.

* **Features**: 
  * Maximum supply of 33,554,432 coins.
  * Allows for transactions with up to 6 decimal places.
  * Blocks are generated approximately every ~3 minutes, with a limit of 2MB per block.
  * Given an average transaction size of 250 bytes (comprising of 5 inputs and 2 outputs), a single block can accommodate approximately ~8300 transactions, which translates to about ~40 transactions per second.

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
  
## Node setup
**Automated configuration and deployment of a Denaro node are facilitated by the `setup.sh` script. It handles system package updates, manages environment variables, configures the PostgreSQL database, sets up a Python virtual environment, installs the required Python dependencies, and initiates the Denaro node. This script ensures that all prerequisites for operating a Denaro node are met and properly configured accoring to the user's preference.**
 
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

 The setup script is designed for Linux distributions that utilize `apt` as their package manager (e.g. Debian/Ubuntu). If system package installation is unsuccessful, it most likely due to the absence of `apt` on your system. This is generally the case for Non-Debian Linux distributions. 
 
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

Once the required packages have been installed, the `--skip-package-install` argument can be used with the setup script to bypass operations which require 'apt', thus mitigating any unsucessful execution relating to package installation.

</dd></dl>
</dd></dl>
</details>

**Setup with Docker**:

  ```bash
  cd path/to/denaro
  cd docker
  docker-compose -f docker-compose.yml up --build -d
  ```

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

## Mining

**Denaro** adopts a Proof of Work (PoW) system for mining:

- **Block Hashing**:
  - Utilizes the sha256 algorithm for block hashing.
  - The hash of a block must begin with the last `difficulty` hexadecimal characters of the hash from the previously mined block.
  - `difficulty` can also have decimal digits, that will restrict the `difficulty + 1`st character of the derived sha to have a limited set of values.

    ```python
    from math import ceil

    difficulty = 6.3
    decimal = difficulty % 1

    charset = '0123456789abcdef'
    count = ceil(16 * (1 - decimal))
    allowed_characters = charset[:count]
    ```

- **Rewards**:
  - Block rewards decrease by half over time until they reach zero.
  - Rewards start at `100` for the initial `150,000` blocks, decreasing in predetermined steps until a final reward of `0.3125` for the `458,733`rd block.
  - After this, blocks do not offer a mining reward, but transaction fees are still applicable. A transaction may also have no fees at all.

- **The `miner.py` script can be used to mine Denaro**:
          
  <details>
  <summary><b>Usage:</b></summary>
  <dl><dd>
  
  - **Syntax**:
      ```bash
      miner.py [-h] [-a ADDRESS] [-n NODE] [-w WORKERS] [-m MAX_BLOCKS]
      ```
  
  - **Arguments**:
        
      * `--address`, `-a` (Required): Your public Denaro wallet address where mining rewards will be sent.

      * `--workers`, `-w` (Optional): The number of parallel processes to run. It's recommended to set this to the number of CPU cores you want to use for mining. Defaults to 1.

      * `--workers`, `-w` (Optional): The number of parallel processes to run. It's recommended to set this to the number of CPU cores you want to use for mining. Defaults to 1.

      * `--max-blocks`, `-m` (Optional): Max number of blocks to mine before exit.

      * `--help`, `-h`: Shows the help message.

  <details>
  <summary><b>Examples:</b></summary>
  <dl><dd>
  
  - #### 1. Basic Mining (Single Core)
    To mine using a single CPU core and the default local node:
    
    ```bash
    python3 miner.py --address YOUR_WALLET_ADDRESS
    ```
  
  - #### 2. Mining with a Remote Node
  
    To mine while connected to a specific public node:
    
    ```bash
    python3 miner.py --address YOUR_WALLET_ADDRESS --node http://a-public-node.com:3006
    ```
  
  - #### 3. Mining with Multiple Cores
  
    To mine using 8 CPU cores for higher performance:
    
    ```bash
    python3 miner.py --address YOUR_WALLET_ADDRESS --workers 8
    ```
  
  *(Replace `YOUR_WALLET_ADDRESS` with your actual Denaro address)*
    
  </dd></dl>
  </dd></dl>
  </details>


## Sync Blockchain

To synchronize a node with the Denaro blockchain, send a request to the `/sync_blockchain` endpoint after starting your node:

```bash
curl http://127.0.0.1:3006/sync_blockchain
```

## Nodeless wallet setup
To setup a nodeless wallet, use [Denaro Wallet Client GUI](https://github.com/The-Sycorax/DenaroWalletClient-GUI).

## License
Denaro is released under the terms of the GNU Affero General Public License v3.0. See [LICENSE](LICENSE) for more information or goto https://www.gnu.org/licenses/agpl-3.0.en.html
