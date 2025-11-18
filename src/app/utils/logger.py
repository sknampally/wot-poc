"""
Logging configuration for the Web of Trust POC.

This module sets up logging to:
- File: logs/wot.log (captures everything, DEBUG and above)
- Console: Configurable via LOG_LEVEL_CONSOLE env var (default: WARNING)

Usage:
    from app.utils.logger import setup_logging, get_logger
    
    setup_logging()  # Initialize once at startup
    log = get_logger(__name__)  # Get logger for your module
    log.info("Something happened")
"""
import logging
import os
from pathlib import Path

# --- LOGGING CONFIGURATION ---

# Place logs/ at project root (same level as src/, data/, etc.)
LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGFILE = LOG_DIR / "wot.log"


def setup_logging():
    """
    Initialize logging configuration.
    
    Sets up two handlers:
    - File handler: Writes all logs (DEBUG+) to logs/wot.log
    - Console handler: Writes logs at configurable level (default: WARNING)
    
    The console log level can be controlled via the LOG_LEVEL_CONSOLE 
    environment variable. Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL.
    
    Returns:
        logging.Logger: The root logger instance
    """
    # Read console log level from environment variable
    # Default to WARNING to keep console output clean
    level_str = os.getenv("LOG_LEVEL_CONSOLE", "WARNING").upper()
    console_level = getattr(logging, level_str, logging.WARNING)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Always capture everything to file

    # Clear previous handlers to prevent duplicates on re-runs
    if logger.hasHandlers():
        logger.handlers.clear()

    # --- File Handler (everything) ---
    # Write all log levels to file for debugging and audit trail
    fh = logging.FileHandler(LOGFILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    # --- Console Handler (configurable) ---
    # Only show important messages on console by default
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch_formatter = logging.Formatter("%(levelname)s: %(message)s")
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)

    # Silence overly verbose third-party library loggers to prevent reentrant logging issues
    # These libraries log at DEBUG level for every HTTP request, which can cause logging conflicts
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)

    logger.info(f"Logging initialized at {LOGFILE}")
    logger.info(f"Console log level: {logging.getLevelName(console_level)}")
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger instance for a specific module.
    
    Args:
        name: Logger name (typically __name__ from the calling module)
    
    Returns:
        logging.Logger: Configured logger instance
    
    Example:
        log = get_logger(__name__)
        log.info("Something happened")
        log.warning("Something concerning")
        log.error("Something went wrong")
    """
    return logging.getLogger(name)
