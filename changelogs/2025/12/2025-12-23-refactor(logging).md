**refactor(logging): implement unified logging system with file rotation and structured output**

**Contributor**: The-Sycorax (https://github.com/The-Sycorax)

**Commit**: [0621cbf6246c799bc27b0fda8fdc4adbee79b423](https://github.com/The-Sycorax/denaro/commit/0621cbf6246c799bc27b0fda8fdc4adbee79b423)

**Date**: December 23rd, 2025

---

## Overview:
  - This refactor introduces a unified logging system that replaces all print statements throughout the codebase with structured logging. The new logging system supports multiple verbosity levels, file rotation, environment-based configuration, thread-safety, and prevention of log injection attacks.

  - Several changes of the codebase have been made to accomindate this refactor. Additionally, Denaro's version has been incremented from `2.0.0` to `2.0.1`.

### New `denaro/logger.py` Module:

  - **`LogManager` Class**:
    - Singleton class that manages logging configuration. It ensures the logging subsystem is initialized exactly once via double-checked locking. It handles 'Rich' console setup and rotating file handlers for persistent storage.
    
    - **`__new__`**: 
      - Creates or returns the existing singleton instance using double-checked locking pattern for thread-safe initialization.
    
    - **`__init__`**: 
      - Initializes the instance state flags (`_configured`, `_initialized`). Returns early if already initialized.
    
    - **`validate_log_format`** (*staticmethod*): 
      - Validates logging format strings against Python logging specifiers using regex. Creates a test formatter and log record to verify format processing. Falls back to default format on validation failure.
    
    - **`validate_date_format`** (*staticmethod*): 
      - Validates date format strings against standard strftime directives using regex. Falls back to default on validation failure.
    
    - **`configure`**: 
      - Configures the root logger with console and file handlers. Sets global logging level, suppresses noisy third-party loggers, creates console handler, and rotating file handler. Uses UTC timezone for log timestamps. Clears handlers before configuration to prevent duplicates.
    
    - **`get_logger`**: 
      - Returns a configured logger instance for a specific module. Automatically triggers configuration if not already done.

    - **`is_configured`** (*property*): 
      - Returns `True` if the logging system has been successfully configured.

    ---

  - **`TerminalSafeFormatter` Class** (*extends `logging.Formatter`*):
    - Formatter class that sanitizes log output. Acts as a defense against Log Injection attacks ([CWE-117](https://cwe.mitre.org/data/definitions/117.html)) and terminal manipulation by stripping ANSI escape sequences and non-printable control characters.
    
    - **`sanitize`** (classmethod): 
      - Removes potentially dangerous characters from provided text. Strips ANSI CSI sequences, carriage returns, and control characters to ensure output is safe.
    
    - **`format`**: 
      - Overrides the base formatter to call `sanitize()` on the formatted output, ensuring all log messages are cleaned before display or storage.

    ---

  - **`DenaroLogHighlighter` Class** (*extends `RegexHighlighter`*):
    - Custom Rich highlighter that applies regex-based coloring to log messages. Protects URL paths and query strings within quoted HTTP request lines from being highlighted to prevent visual spoofing.
    
    - **`base_style`**: 
      - Class attribute defining the base style prefix (`denaro.`) for all highlight patterns.
    
    - **`highlights`**: 
      - Class attribute containing regex patterns for HTTP methods, HTTP versions, IP addresses, log levels, logger names, status codes, tags, timestamps, URLs, and network errors.
    
    - **`_get_protected_segments`** (*classmethod*): 
      - Identifies regions in a log string that should NOT be highlighted. Targets URL paths in quoted request lines to avoid coloring user-controlled input. Returns a list of (start and end) indices.
    
    - **`_overlaps`** (*staticmethod*): 
      - Checks if two ranges (A and B) intersect.
    
    - **`highlight`**: 
      - Overrides the base `highlight` method. Applies standard regex highlighting, then filters out spans that overlap with protected segments identified by `_get_protected_segments`.

    ---
    
  - ### **Module Level Elements**:
    - **`_manager`**: 
      - Private singleton instance of `LogManager`, auto-configured on module import.

    - **`get_logger`** (*function*): 
      - Public accessor to the logging system. Delegates to `_manager.get_logger()`, ensuring configuration is applied. Takes module name as argument, returns configured `logging.Logger` instance.
    
    ---

  - ### **Constants**:
    - **`PROJECT_ROOT`**: Path to project root directory.
    
    - **`LOG_FILE_PATH`**: Path to log file.

    - **Environment Variables** (*from `denaro/constants.py`*):
      - **`LOG_LEVEL`**: Logging verbosity level (default: `'INFO'`).
        
      - **`LOG_FORMAT`**: Log message format (default: `'%(asctime)s - %(levelname)s - %(name)s - %(message)s'`).
        
      - **`LOG_DATE_FORMAT`**: Date format for log timestamps (default: `'%Y-%m-%dT%H:%M:%S'`).
        
      - **`LOG_CONSOLE_HIGHLIGHTING`**: Enables Rich console highlighting (default: `True`).
        
      - **`LOG_INCLUDE_REQUEST_CONTENT`**: Includes HTTP request body in logs (default: `False`).
        
      - **`LOG_INCLUDE_RESPONSE_CONTENT`**: Includes HTTP response body in logs (default: `False`).
        
      - **`LOG_INCLUDE_BLOCK_SYNC_MESSAGES`**: Includes verbose block sync logs (default: `False`).
    
      - **`LOG_MAX_FILE_SIZE`**: Maximum log file size before rotation (10MB).
      
      - **`LOG_MAX_PATH_LENGTH`**: Maximum URL path length to log (320 chars).
      
      - **`LOG_BACKUP_COUNT`**: Number of backup log files to keep (5).

---

## Updated Files:

All of the files listed below have been updated to use the new logger module, and all of their print statements replaced with structured logging calls. Various log messages in these files have also been updated.

  - **`denaro/node/main.py`**
  - **`denaro/node/nodes_manager.py`**
  - **`denaro/manager.py`**
  - **`denaro/database.py`**
  - **`denaro/consensus.py`**
  - **`denaro/transactions/transaction.py`**
  - **`denaro/node/identity.py`**

---

## **Other Changes**:

- ###  **`denaro/node/main.py`**:
  - Updated FastAPI app initialization to use `DENARO_SELF_URL`. It also includes `title`, `description`, `version` metadata.
  
  - **New `log_requests()` middleware**:
    
    - This function is meant to log all incoming requests to the node and outgoing responses from the node.
    
    - **Log Formatting**:
      - **Incoming requests**: 
        - `<-- {CLIENT_IP} - "{METHOD} {PATH} HTTP/1.1"`
      - **Outgoing responses**: 
        - `--> {CLIENT_IP} - "{METHOD} {PATH} HTTP/1.1" {STATUS_CODE} ({TIME}s)`
      - **Errors**: 
        - `--> {CLIENT_IP} - "{METHOD} {PATH} HTTP/1.1" ERROR ({TIME}s): {error}`
      - **Request body**: 
        - `Outgoing Request:\n{body}`
      - **Response body**: 
        - `Incoming Response:\n{body}`
      - Logged `{PATH}` is truncated with `...[TRUNCATED]` when it's character length exceeds `LOG_MAX_PATH_LENGTH`.
      
  ---

- ### **`denaro/node/nodes_manager.py`**:
  - **Updated `request()` method**:
    - This method has been updated to log all outgoing requests from the node and incoming responses to the node.
    - The method signature has been updated to accept `node_id` and `signed` parameters.
    - **Log Formatting**:
      - **Outgoing requests**: 
          - `--> "{METHOD} {URL} HTTP/1.1" [SIGNED]`
      - **Incoming responses**: 
          - `<-- "{METHOD} {URL} HTTP/1.1" {STATUS_CODE} ({TIME}s)`
      - **Errors**: 
          - `<-- "{METHOD} {URL} HTTP/1.1" {STATUS_CODE} ERROR ({TIME}s): {error}`
      - **Request body**: 
          - `Outgoing Request:\n{body}`
          - Logged when `LOG_INCLUDE_REQUEST_CONTENT` is enabled.
      - **Response body**: 
          - `Incoming Response:\n{body}`
          - Logged when `LOG_INCLUDE_RESPONSE_CONTENT` is enabled.
      - Logged `{PATH}` is truncated with `...[TRUNCATED]` when it's character length exceeds `LOG_MAX_PATH_LENGTH`.
    
  ---
  
- ### **`run_node.py`**:
  - Now suppresses uvicorn's default logging.
  - Changed `reload=True` to `reload=False`.
    
  ---

- ### **`denaro/transactions/transaction.py`**:
  - Updated `_verify_outputs` method to remove legacy double-spend patch.

  ---

- ### **`setup.sh`**: 
  - Added new console prompts to set logging variables.
  - Min port number has been updated to `1`.
  - Max port number has been updated to `65535`.
  - Updated node startup command to `python3 run_node.py`.

  ---
  
- ### **`docker/docker-entrypoint.sh`**: 
  - Added new exports for logging variables.

  ---

- ### **`.env.example`**:
  - Added environment variables for logging.

  ---

- ### **`.gitignore`**:
  - Added `logs/` directory to ignore log files from version control.

  ---

- ### **`requirements.txt`**: 
  - Added `rich` library for console highlighting.