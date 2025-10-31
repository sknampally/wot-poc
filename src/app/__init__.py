# src/app/__init__.py
from pathlib import Path

# project root = parent of src/
ROOT_DIR  = Path(__file__).resolve().parents[2]
SRC_DIR   = ROOT_DIR / "src"
DATA_DIR  = ROOT_DIR / "data"
LOG_DIR   = ROOT_DIR / "logs"   # << now at project root

CACHE_DIR   = DATA_DIR / "cache"
INPUT_XLSX  = DATA_DIR / "input.xlsx"
OUTPUT_XLSX = DATA_DIR / "output.xlsx"

# create dirs if missing
for d in (DATA_DIR, LOG_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)
