# src/app/core/schema.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

import pandas as pd

__all__ = [
    "load_headers",
    "name_header",
    "coerce_to_headers",
    "normalize_status",
    "normalize_fd",
    "normalize_year",
]

# -----------------------------
# Header loading & name column
# -----------------------------

def load_headers(xlsx_path: Path) -> List[str]:
    """
    Read the first sheet of the client input workbook and return the header row as a list of strings.
    """
    df = pd.read_excel(xlsx_path, engine="openpyxl")
    headers = [str(c).strip() for c in df.columns.tolist()]
    return headers


def name_header(headers: List[str]) -> str:
    """
    Choose the best-matching 'name' column from the provided headers.
    Fallback to the very first header if nothing matches.
    """
    candidates = [
        "Product Name", "Project Name", "Name", "Initiative", "Program", "Title",
    ]
    lower_map = {h.lower(): h for h in headers}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return headers[0] if headers else "Name"


# -----------------------------
# Normalizers used elsewhere
# -----------------------------

_STATUS_ENUMS = {"announced", "pilot", "launched", "discontinued"}

def normalize_status(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    low = s.lower()
    for enum in _STATUS_ENUMS:
        if enum in low:
            return enum.capitalize()
    # common synonyms
    if "live" in low or "prod" in low:
        return "Launched"
    if "beta" in low or "pilot" in low or "trial" in low:
        return "Pilot"
    if "announce" in low or "press" in low or "coming" in low:
        return "Announced"
    if "end" in low or "retire" in low or "sunset" in low or "closed" in low:
        return "Discontinued"
    return s  # unknown label, keep as-is


def normalize_fd(v: Any) -> str:
    """
    Ternary normalization: True / False / Failed to disclose
    """
    s = str(v or "").strip()
    if not s:
        return "Failed to disclose"
    low = s.lower()
    if low in {"true", "yes", "y", "1"}:
        return "True"
    if low in {"false", "no", "n", "0"}:
        return "False"
    if "unknown" in low or "n/a" in low or "not disclosed" in low or "undisclosed" in low:
        return "Failed to disclose"
    return s


_year_re = re.compile(r"\b(19|20)\d{2}\b")

def normalize_year(v: Any) -> str:
    """
    Extract/format a 4-digit year if present, otherwise return "".
    """
    s = str(v or "").strip()
    if not s:
        return ""
    m = _year_re.search(s)
    return m.group(0) if m else ""


# -----------------------------
# Coercion into sheet headers
# -----------------------------

def _canonicalize_key(k: str) -> str:
    """
    Lower-case + collapse spaces/punctuation to make fuzzy matching between model keys and sheet headers.
    """
    k = (k or "").strip().lower()
    # replace non-alphanum with single space, then collapse
    k = re.sub(r"[^a-z0-9]+", " ", k)
    k = re.sub(r"\s+", " ", k).strip()
    return k


def coerce_to_headers(
    obj: Dict[str, Any],
    headers: List[str],
    project_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map an arbitrary dict `obj` (from the LLM) into a row keyed by the exact `headers`.
    - Case/space/punct insensitive header matching
    - Preserves `_evidence` list if provided
    - Ensures all headers present
    - Optionally forces name column to `project_name`
    """
    row: Dict[str, Any] = {}

    # Build a canonical lookup for the sheet headers
    header_canon: Dict[str, str] = {}
    for h in headers:
        header_canon[_canonicalize_key(h)] = h

    # First pass: attempt direct or canonical matches
    for k, v in (obj or {}).items():
        if k == "_evidence":
            # handled later
            continue
        if k in headers:
            row[k] = v
            continue
        ck = _canonicalize_key(k)
        if ck in header_canon:
            row[header_canon[ck]] = v

    # Ensure all headers exist
    for h in headers:
        if h not in row:
            row[h] = ""

    # Evidence passthrough
    ev = obj.get("_evidence", [])
    if isinstance(ev, list):
        row["_evidence"] = ev
    else:
        row["_evidence"] = []

    # Force name column if requested
    if project_name:
        nh = name_header(headers)
        row[nh] = project_name

    return row
