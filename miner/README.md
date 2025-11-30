## Denaro CUDA Miner Setup and Usage

`cuda_miner.py` is a GPU accelerated single worker mining script for Denaro. It uses PyCUDA to JIT (Just in Time) compile a CUDA kernel for a SHA256 based nonce search over a constant block prefix and submits candidate blocks to a specified Denaro node.

---

## 1. Prerequisites:

- ### Hardware:
    - NVIDIA GPU with CUDA support.

- ### OS:
    - Linux x86-64
    - *Windows and WSL2 may work but are not covered here.*

- ### Software:
    - **NVIDIA Driver** compatible with your CUDA Toolkit.
    - **CUDA Toolkit** **11.8 or newer** (for modern GPUs).
    - **Python** 3.8+.

---

## 2. CUDA Toolkit Setup (Linux):

1. **Install NVIDIA Driver + CUDA Toolkit:**  
   - Follow NVIDIA’s official Linux installation guide for your distribution: [CUDA Installation Guide for Linux](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/)

   - Ensure your CUDA Toolkit installation is compatiable with your GPU architechture.

2. **Validate the installation:**
   ```bash
   nvidia-smi
   nvcc --version
   ````

3. **Ensure CUDA is discoverable:**
   - Add the following to your shell profile if missing:

       ```bash
       export PATH=/usr/local/cuda/bin:${PATH}
       export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}
       ```


---

## 3. Python Environment (Optional):

- Only perform this step if you do not already have Python virtual environment setup for Denaro.
    - For more details refer to : [README.md](../README.md).

1. **Create and activate a Python virtual environment:**

   ```bash
   python3 -m venv ../venv
   source ../venv/bin/activate
   python -m pip install --upgrade pip
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

---

## 4. Running the Miner

### Usage:
- **Syntax:**
  ```bash
  cuda_miner.py [-h] --address ADDRESS [--node NODE] [--max-blocks MAX_BLOCKS] [--gpu-blocks BLOCKS] [--gpu-threads THREADS] [--gpu-iterations ITERS_PER_THREAD] [--gpu-arch GPU_ARCH]
  ```

- **Options:**        
    * `--address`, `-a`: Mining address to receive rewards *(required)*.
    
    * `--node`, `-n`: URL of the Denaro node API (Default: http://127.0.0.1:3006/).
    
    * `--max-blocks`, `-m`: Max number of blocks to mine before exit (Default: 10).
    
    * `--gpu-blocks`: CUDA grid blocks per launch (Default: 256).
    
    * `--gpu-threads`: CUDA threads per block (Default: 256).
    
    * `--gpu-iterations`: Iterations per thread per kernel batch (Default: 10000).
    
    * `--gpu-arch`: Sets the `nvcc` architecture flag (Default: sm_89).
    
      - To determine the correct architecture flag for your GPU, refer to: [SM architecture reference](https://arnon.dk/matching-sm-architectures-arch-and-gencode-for-various-nvidia-cards/).

- **Example:**

  ```bash
    python3 cuda_miner.py -a DZ8CxkXKwcnwQh6aXidBBWE75qvymfg4zZfLqZVP6Qh5A --gpu-arch sm_86
  ```

---

## 5. GPU Tuning Guide:

This section provides guidance on how to fine tune the existing GPU settings to improve speed, stability, and system responsiveness. 

- **Only do this if you know what you are doing, otherwise use the default settings.**

- The parameters below map directly to the script’s CLI flags and is specific to this script’s CUDA launch model (single worker, PyCUDA JIT):

  - **Blocks**: (`--gpu-blocks`) Increasing this parameter increases the number of independent work units available to the scheduler. More blocks can hide latency when the GPU has headroom. However if the GPU is saturated or register limited, more blocks can add overhead without any apparent gain.
  
  - **Threads**: (`--gpu-threads`)  The practical default for this parameter is **256**. When setting this parameter, use multiples of **32** but do not exceed **1024**.

  - **Iterations**: (`--gpu-iterations`) For this parameter, higher values reduce host overhead (fewer launches) but lengthen each kernel, which can cause desktop stutter and slow response to new work.

  - **Kernel Batch Size** : `Blocks × Threads × Iterations` attempted nonces in one kernel launch. In this script each thread advances the nonce by `global_step = Blocks × Threads` on every iteration, so `Iterations` directly scales single-kernel runtime.

  ---

- **5.1 Default Profile**:
  - When running the miner, use the default GPU settings first, and then fine tune them based on observed behavior:
    - Blocks: **256**
    - Threads per block: **256**
    - Iterations per thread: **10,000**

  - Approximate work per batch:
    - Global threads = `256 × 256 = 65,536`
    - Attempts per batch = `65,536 × 10,000 ≈ 6.55e8`

  - You can also choose the closest profile to your hardware. However it is safer to use the default settings first before adjusting any parameters.

    | **Hardware class**                  | **Blocks** | **Threads** | **Iterations** |
    |-------------------------------------|:----------:|:-----------:|:--------------:|
    | Laptops or older desktop GPUs       |    128     |     256     |      5000      |
    | Most mainstream desktop GPUs        |    256     |     256     |     10000      |
    | Faster desktop GPUs                 |    512     |     256     |     15000      |
    | RTX 40 or 50 Series                 |    1024    |     512     |    20,000      |

  ---

- **5.2 Practical Procedure**:
  -  Run the miner with the default values for **60–90 seconds** while monitoring GPU utilization, clocks, temperature, and power:
      ```bash
      nvidia-smi --loop=1 --query-gpu=temperature.gpu,utilization.gpu,power.draw,clocks.sm --format=csv
      ```

  - **If utilization is low and the system is responsive**:
    - Increase **Iterations** in small steps (e.g., +2,000 to +5,000). 
    - If utilization remains low, increase **Blocks** in small steps (e.g., +64 or +128). 
    - Recheck temperature and interactivity after each change.
  
  - **If you see lag, stutter, or a driver reset**:
    - Reduce **Iterations** by **25–50%**.
    - If needed, reduce **Blocks** (e.g., 256 → 192 → 128) to ease scheduler pressure.  
    - If you encounter launch/resource errors or unusually low occupancy, set **Threads** to **128** and retest.
     
  - Repeat the process until you reach a stable point with acceptable utilization and temperature. Prefer changing **Iterations** first. Then change **Blocks**. Finally, change **Threads** only when resource limits require it.

    ---

  - **What to watch while adjusting parameters**:
    - Utilization: 
      - A healthy miner shows high multiprocessor utilization with minimal dips. Sustained low utilization with low temperature often means the kernel is too short or the grid is too small.
    - Clocks:
      - Stable multiprocessor clocks indicate the workload is within power and thermal budgets. Large drops mean power or thermal limits are active.
    - Temperature and power:
      - Keep steady state temperature comfortably below the throttle point. If power draw pegs near the limit, shorten the kernel with fewer **Iterations**.


  ---

- **5.3 Estimating a target kernel duration (optional)**:
  - If you the know approximate throughput (`H`) in hashes per second and you want a target single kernel time (`T`) in seconds:
    - Attempts per batch: `Blocks × Threads × Iterations`
    - Choose `Iterations = (H × T) / (Blocks × Threads)`
  - Example:
    - If `H = 400 MH/s`, `Blocks = 256`, `Threads = 256`, and target `T = 1.5 s`, then `Iterations = (400e6 × 1.5) / (256 × 256) = 9,155`. Use 9,200 as a rounded value.
  - If `H` is unknown, apply the procedures in 5.2 and monitor the GPU while you adjust the parameters.

  ---

- **5.4 Targets and limits**:
  - **Display-attached GPUs**: 
      - Keep kernel batches short to avoid UI stutter and watchdog/TDR timeouts. Prefer moderate **Iterations**. Make gradual changes and test responsiveness after each adjustment.
  
  - **Headless or secondary GPUs**:
    - Longer kernel batches are acceptable if temperatures, clocks, and stability remain within limits.
  
  - **Thermals and power**:
    - If you approach limits, lower **Iterations** first to shorten kernel batches, then lower **Blocks** if needed. This reduces sustained load without changing correctness.
  
  ---

- **5.5 Troubleshooting**:
  - **Desktop lags, stutters, or triggers a driver reset**:
    - Lower **Iterations** (e.g., 10,000 → 7,500 → 5,000).
    - If necessary, lower **Blocks** (e.g., 256 → 192 → 128).
    - As a last step, set **Threads** to **128** to reduce per-block resource pressure and allow more resident blocks per streaming multiprocessor.

  - **Low GPU utilization (system remains stable)**:
    - Increase **Blocks** in small increments to deepen the work queue.
    - Optionally increase **Iterations** to amortize launch overhead and lengthen kernels within safe time limits.

  - **Launch failures / resource errors / poor occupancy**:
    - Reduce **Threads** (256 → 128) to ease register pressure and increase the number of active blocks per streaming multiprocessor.
    - Reduce **Blocks** if errors persist or if the device reports insufficient resources for the launch.

  - **High temperatures or loud fans**:
    - Reduce **Iterations** to shorten kernels and lower sustained load.
    - If needed, also reduce **Blocks** or improve cooling. Confirm that power limiting is not capping clocks.



