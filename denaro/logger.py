"""
Denaro Logging System
=====================

A unified, thread-safe logging utility for Denaro. This module integrates with
the standard Python `logging` library and the `rich` library to provide structured,
safe, and visually distinct logging outputs.

Usage:
    >>> from denaro.logger import get_logger
    >>> logger = get_logger(__name__)
    >>> logger.info("Application started")
"""

import logging
import logging.handlers
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional, List, Tuple

from rich.console import Console
from rich.highlighter import RegexHighlighter
from rich.logging import RichHandler
from rich.theme import Theme

from .constants import (
    LOG_LEVEL,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_MAX_FILE_SIZE,
    LOG_BACKUP_COUNT,
    LOG_CONSOLE_HIGHLIGHTING,
)


# Define log file location relative to the project root
PROJECT_ROOT = Path(__file__).parent.parent
LOG_FILE_PATH = PROJECT_ROOT / "logs" / "denaro.log"


class LogManager:
    """
    Manages logging configuration via the Singleton pattern.

    This class ensures that the logging subsystem is initialized exactly once.
    It handles the setup of 'Rich' console and rotating file handlers for
    persistent storage.

    Attributes:
        _instance (LogManager): The singleton instance.
        _lock (threading.Lock): Thread lock for atomic initialization.
    """
    
    _instance: Optional["LogManager"] = None
    _lock: threading.Lock = threading.Lock()
    

    def __new__(cls) -> "LogManager":
        """Creates or returns the existing singleton instance."""
        # Double-checked locking pattern for thread-safe singleton initialization
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    

    def __init__(self) -> None:
        """Initializes the LogManager instance."""
        if self._initialized:
            return
        self._configured = False
        self._initialized = True
    

    @staticmethod
    def validate_log_format(log_format: str) -> str:
        """
        Validates the syntax of a logging format string.

        This checks if the format string contains valid Python logging specifiers
        and attempts to format a dummy record to catch runtime errors.

        Args:
            log_format (str): The logging format string (e.g., "%(asctime)s - %(message)s").

        Returns:
            str: The validated format string, or the default `LOG_FORMAT` if validation fails.
        """
        try:
            if not log_format:
                return str(LOG_FORMAT.default())

            log_format = str(log_format)

            # This regex looks for valid keys inside format specifiers
            format_specifier_pattern = r"\([a-zA-Z_][a-zA-Z0-9_]*\)[a-zA-Z]"
            paren_pattern = re.compile(format_specifier_pattern)
            
            # Ensure every match is preceded by a '%' char
            for match in paren_pattern.finditer(log_format):
                start_pos = match.start()
                if start_pos == 0 or log_format[start_pos - 1] != "%":
                    raise ValueError("Malformed format specifier.")

            # Test formatting against a dummy record to catch runtime errors
            formatter = logging.Formatter(fmt=log_format)
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="test", args=(), exc_info=None,
            )
            formatted_output = formatter.format(record)

            # If the output still contains format specifiers, python didn't process them
            if re.search(format_specifier_pattern, formatted_output):
                raise ValueError("Format specifiers not properly processed.")

            return log_format
        except Exception as e:
            # Fallback to default format on validation failure to ensure logging continuity
            try:
                df = str(LOG_DATE_FORMAT.default())
            except Exception:
                df = "%Y-%m-%d %H:%M:%S"
            print(
                f"{time.strftime(df)} - denaro.logger - Validation Error: {e}. Using default.",
                file=sys.stderr,
            )
            return str(LOG_FORMAT.default())


    @staticmethod
    def validate_date_format(date_format: str) -> str:
        """
        Validates the syntax of a date format string against standard strftime directives.

        Args:
            date_format (str): The date format string (e.g., "%Y-%m-%d").

        Returns:
            str: The validated date format string, or default if validation fails.
        """
        if not date_format:
            return str(LOG_DATE_FORMAT.default())

        date_format = str(date_format)

        # Regex strictly matches valid strftime directives (e.g., %Y, %m, %d)
        # and standard separators to prevent injection of arbitrary text.
        date_format_pattern = re.compile(
            rf"^(?=.*%(?!%)(?:[EO])?(?:[-_0^#])*(?:[A-DF-HIM-NPR-VW-Za-hj-lm-npr-uw-z]))"
            rf"(?:%%|%(?:[EO])?(?:[-_0^#])*(?:[A-DF-HIM-NPR-VW-Za-hj-lm-npr-uw-z])|[0-9 \t:\-\/\.,TZ+])+$"
        )
        
        # Fallback to default format on validation failure to ensure logging continuity
        if not date_format_pattern.match(date_format):
            print(
                f"{time.strftime(str(LOG_DATE_FORMAT.default()))} - denaro.logger - "
                f"Invalid date format. Using default.",
                file=sys.stderr,
            )
            return str(LOG_DATE_FORMAT.default())

        return date_format


    def configure(
        self,
        log_level: Optional[str] = None,
        log_file: Optional[Path] = None,
        console_output: bool = True,
        file_output: bool = True,
    ) -> None:
        """
        Configures the root logger with console and file handlers.
        
        This sets the global logging level, suppresses noisy third-party libraries,
        and attaches formatters.

        Args:
            log_level (Optional[str]): Logging level (DEBUG, INFO, etc.). Defaults to env var.
            log_file (Optional[Path]): Absolute path to log file. Defaults to `logs/denaro.log`.
            console_output (bool): Enable stdout logging. Defaults to True.
            file_output (bool): Enable rotating file logging. Defaults to True.
        """
        with self._lock:
            if self._configured:
                return

            level_str = log_level or LOG_LEVEL.default()
            numeric_level = getattr(logging, str(level_str).upper(), logging.INFO)

            root_logger = logging.getLogger()
            root_logger.setLevel(numeric_level)

            # Suppress excessive logs from networking and server libraries
            # This keeps the console clean for application-specific logic
            for lib in ["httpx", "httpx._client", "uvicorn.access"]:
                logging.getLogger(lib).setLevel(logging.WARNING)
            for lib in ["uvicorn.error", "uvicorn", "uvicorn.asgi"]:
                logging.getLogger(lib).setLevel(logging.ERROR)

            root_logger.handlers.clear()

            log_format = self.validate_log_format(LOG_FORMAT)
            date_format = self.validate_date_format(LOG_DATE_FORMAT)

            # Uses UTC for consistency across different server timezones
            file_formatter = TerminalSafeFormatter(fmt=log_format, datefmt=date_format + " UTC")
            file_formatter.converter = time.gmtime

            if console_output:
                if LOG_CONSOLE_HIGHLIGHTING:
                    # 'Rich' theme definition for highlighting
                    denaro_theme = Theme(
                        {
                            "denaro.arrow":           "bold yellow",
                            "denaro.http_version":    "bold dim",
                            "denaro.ip":              "cyan",
                            "denaro.level_critical":  "bold red reverse",
                            "denaro.level_debug":     "bold dim",
                            "denaro.level_error":     "bold red",
                            "denaro.level_info":      "bold green",
                            "denaro.logger_name":     "magenta",
                            "denaro.level_warning":   "bold yellow",
                            "denaro.method":          "bold white",
                            "denaro.network_error":   "bold red",
                            "denaro.status_critical": "bold red reverse",
                            "denaro.status_error":    "bold red",
                            "denaro.status_redirect": "bold yellow",
                            "denaro.status_success":  "bold green",
                            "denaro.status_sync":     "bold green",
                            "denaro.tag":             "bold magenta",
                            "denaro.timestamp":       "bold cyan",
                            "denaro.url":             "cyan",
                            "denaro.url_host":        "cyan",
                        }
                    )

                    console = Console(theme=denaro_theme, highlight=False, width=256, force_terminal=True)

                    rich_handler = RichHandler(
                        console=console,
                        highlighter=DenaroLogHighlighter(),
                        keywords=[],
                        rich_tracebacks=True,
                        omit_repeated_times=False,
                        show_path=False,
                        enable_link_path=True,
                        show_time=False,
                        show_level=False,
                        markup=False,
                    )
                    rich_handler.setLevel(numeric_level)
                    rich_handler.setFormatter(file_formatter)
                    root_logger.addHandler(rich_handler)
                else:
                    # Fallback to standard stream handler if highlighting is disabled
                    console_handler = logging.StreamHandler(sys.stdout)
                    console_handler.setLevel(numeric_level)
                    console_handler.setFormatter(file_formatter)
                    root_logger.addHandler(console_handler)

            if file_output:
                log_file_path = log_file or LOG_FILE_PATH
                log_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Configures file handler
                file_handler = logging.handlers.RotatingFileHandler(
                    filename=str(log_file_path),
                    maxBytes=LOG_MAX_FILE_SIZE,
                    backupCount=LOG_BACKUP_COUNT,
                    encoding="utf-8",
                )

                file_handler.setLevel(numeric_level)
                file_handler.setFormatter(file_formatter)
                root_logger.addHandler(file_handler)

            self._configured = True


    def get_logger(self, name: str) -> logging.Logger:
        """
        Retrieves a configured logger instance for a specific module.

        Args:
            name (str): The name of the logger (typically `__name__`).

        Returns:
            logging.Logger: A configured standard Python logger.
        """
        if not self._configured:
            self.configure()
        return logging.getLogger(name)


    @property
    def is_configured(self) -> bool:
        """Returns True if the logging system has been successfully configured."""
        return self._configured


class TerminalSafeFormatter(logging.Formatter):
    """
    A formatter class that sanitizes log output.

    This formatter acts as a defense against Log Injection attacks (CWE-117)
    and terminal manipulation by stripping ANSI escape sequences and non-printable 
    control characters.
    """

    # Matches ANSI CSI sequences (colors, cursor moves) and single ESC chars
    _ansi_escape_re = re.compile(
        r"\x1b\[[0-?]*[ -/]*[@-~]"
        r"|\x1b[@-Z\\-_]"
    )
    # Matches control chars (0x00-0x1F) excluding Tab and Newline
    _control_chars_re = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
    _carriage_return_re = re.compile(r"\r")


    @classmethod
    def sanitize(cls, text: str) -> str:
        """
        Removes potentially dangerous characters from the provided text.
        
        Args:
            text (str): The raw log message.
            
        Returns:
            str: The sanitized message safe for terminal output.
        """
        if not text:
            return text
        text = cls._ansi_escape_re.sub("", text)
        text = cls._carriage_return_re.sub("", text)
        text = cls._control_chars_re.sub("", text)
        return text


    def format(self, record: logging.LogRecord) -> str:
        return self.sanitize(super().format(record))


class DenaroLogHighlighter(RegexHighlighter):
    """
    Custom Rich Highlighter for API and system logs.

    This class applies regex-based coloring to log messages. To prevent visual 
    spoofing, it strictly protects URL paths and query strings within quoted
    HTTP request lines from being highlighted.
    """

    base_style = "denaro."
    highlights = [
        r"(?P<arrow>(\-\->)|(<--)|(╠>)|(╔>)|(╚>))",
        r"(?P<http_version>HTTP/\d(?:\.\d)?)",
        r"(?P<ip>(?<!//)(?<!\d)\b((?:\d{1,3}\.){3}\d{1,3}(?::\d+)?)\b)",
        r"(?P<level_critical>\bCRITICAL\b)",
        r"(?P<level_debug>\bDEBUG\b)",
        r"(?P<level_error>\bERROR\b)",
        r"(?P<level_info>\bINFO\b)",
        r"\-\s+\w+\s+-\s+(?P<logger_name>[\w.]+)(?=\s-\s)",
        r"(?P<level_warning>\bWARNING\b)",
        r"(?P<method>\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\b)",
        r"(?P<network_error>NETWORK_ERROR)",
        r"(?P<status_critical>(?<!\.)\b5\d{2}\b⁢(?!\.))", # 5xx
        r"(?P<status_error>(?<!\.)\b4\d{2}\b⁢(?!\.))",    # 4xx
        r"(?P<status_sync>(?<!\.)\b409\b⁢(?!\.))",
        r"(?P<status_redirect>(?<!\.)\b3\d{2}\b⁢(?!\.))", # 3xx
        r"(?P<status_success>(?<!\.)\b2\d{2}\b⁢(?!\.))",  # 2xx
        r"(?P<tag>\[.*?\])",
        r"(?P<timestamp>^(.*?)UTC)",
        r"(?P<url>https?://\S+)",            # Standalone URLs
        r"(?P<url_host>https?://[^/\s]+/?)", # Host part only
    ]

    # Greedy matching captures up to the *last* 'HTTP/x.x' to prevent
    # attackers from injecting fake HTTP versions in the URL path.
    _quoted_request_line_re = re.compile(
        r'"(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(.+)\s+(HTTP/\d(?:\.\d)?)"',
        re.IGNORECASE,
    )


    @classmethod
    def _get_protected_segments(cls, s: str) -> List[Tuple[int, int]]:
        """
        Identifies regions in a log string that should NOT be highlighted.
        
        This specifically targets URL paths in quoted request lines to avoid
        coloring user-controlled input.
        
        Args:
            s (str): The plain text log message.
            
        Returns:
            List[Tuple[int, int]]: List of (start, end) indices for protected segments.
        """
        protected = []
        for m in cls._quoted_request_line_re.finditer(s):
            url = m.group(2)
            url_start = m.start(2) 
            
            if url.startswith('http://') or url.startswith('https://'):
                # For full URLs, allow highlighting of the `scheme://host`,
                # but protect the path starting from the first slash.
                scheme_end = url.find('://') + 3
                first_path_slash = url.find('/', scheme_end)
                if first_path_slash != -1:
                    protect_start = url_start + first_path_slash + 1
                    protect_end = url_start + len(url)
                    if protect_start < protect_end:
                        protected.append((protect_start, protect_end))
            else:
                # For relative paths, protect the entire URL string
                protect_start = url_start
                protect_end = url_start + len(url)
                protected.append((protect_start, protect_end))
        return protected


    @staticmethod
    def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
        """Check if range A intersects with range B."""
        return a_start < b_end and a_end > b_start


    def highlight(self, text) -> None:
        """
        Applies highlighting, filtering out spans that overlap with protected segments.
        """
        # Applies standard regex highlighting
        super().highlight(text)

        # Identifies segments that must remain plain (e.g. URL paths)
        plain = text.plain
        protected_segments = self._get_protected_segments(plain)
        if not protected_segments:
            return

        spans = getattr(text, "spans", None)
        if not spans:
            return

        # Filters existing highlight spans against protected segments
        filtered = []
        for span in spans:
            if any(self._overlaps(span.start, span.end, ps, pe) for ps, pe in protected_segments):
                continue
            filtered.append(span)

        text.spans = filtered


_manager = LogManager()

def get_logger(name: str) -> logging.Logger:
    """
    Public accessor of the logging system.
    Delegates to the Singleton LogManager, ensuring configuration is applied.
    
    Args:
        name (str): The name of the module requesting the logger.
        
    Returns:
        logging.Logger: The configured logger instance.
    """
    return _manager.get_logger(name)

# Auto-configure on import to ensure immediate availability
_manager.configure()