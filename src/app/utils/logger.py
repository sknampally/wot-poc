# src/app/utils/logger.py
from __future__ import annotations
import logging, os
from pathlib import Path
from typing import Optional

_LOGGER: Optional[logging.Logger] = None

def _project_root() -> Path:
    # repo root is two levels up from this file: src/app/utils/logger.py
    return Path(__file__).resolve().parents[3]

def get_log_dir() -> Path:
    # Put logs at "<repo>/logs"
    root = _project_root()
    d = root / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def setup_logging(level: int = logging.INFO) -> Path:
    """
    Initialize root logging:
      • File: logs/wot.log (DEBUG+)
      • Console: INFO+ (concise)
    Returns the logfile path.
    """
    global _LOGGER
    logfile = get_log_dir() / "wot.log"

    # Reset any existing handlers (avoid duplicate lines when reloading)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG)  # capture everything centrally

    # File handler (full detail)
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ffmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(ffmt)
    root.addHandler(fh)

    # Console handler (concise)
    ch = logging.StreamHandler()
    ch.setLevel(level)
    cfmt = logging.Formatter("%(levelname)s: %(message)s")
    ch.setFormatter(cfmt)
    root.addHandler(ch)

    _LOGGER = logging.getLogger("main")
    _LOGGER.info("Logging initialized at %s", logfile)
    return logfile

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
