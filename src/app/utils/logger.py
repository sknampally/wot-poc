# src/app/utils/logger.py
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
    Initializes logging:
      - File logs everything (DEBUG and above)
      - Console level configurable via env var LOG_LEVEL_CONSOLE
        (default = WARNING)
    """
    # Read console log level from environment variable
    level_str = os.getenv("LOG_LEVEL_CONSOLE", "WARNING").upper()
    console_level = getattr(logging, level_str, logging.WARNING)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Always capture everything

    # Clear previous handlers to prevent duplicates on re-runs
    if logger.hasHandlers():
        logger.handlers.clear()

    # --- File Handler (everything) ---
    fh = logging.FileHandler(LOGFILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    # --- Console Handler (configurable) ---
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch_formatter = logging.Formatter("%(levelname)s: %(message)s")
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)

    logger.info(f"Logging initialized at {LOGFILE}")
    logger.info(f"Console log level: {logging.getLevelName(console_level)}")
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger instance for a specific module.
    Example:
        log = get_logger(__name__)
        log.info("Something happened")
    """
    return logging.getLogger(name)
