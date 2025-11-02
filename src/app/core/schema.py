"""
Schema utilities for handling Excel headers and data normalization.

This module provides:
- Header loading from Excel files
- Field name matching (fuzzy matching for LLM output)
- Data normalization (status, ternary, year fields)
- Conversion of LLM output dicts to Excel-compatible rows

The normalization functions ensure consistency when comparing
AI-extracted data with manual data.
"""
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
    Load column headers from the first sheet of an Excel file.
    
    Args:
        xlsx_path: Path to the Excel file
    
    Returns:
        List[str]: List of column header names (as strings)
    
    Note:
        Uses openpyxl engine to read Excel files. Strips whitespace
        from header names.
    """
    df = pd.read_excel(xlsx_path, engine="openpyxl")
    headers = [str(c).strip() for c in df.columns.tolist()]
    return headers


def name_header(headers: List[str]) -> str:
    """
    Find the best-matching 'name' column from a list of headers.
    
    Searches for common name column variations:
    - Product Name, Project Name, Name, Initiative, Program, Title
    
    Args:
        headers: List of column header names
    
    Returns:
        str: The matching header name, or first header if no match found
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

# Valid status enum values (case-insensitive matching)
_STATUS_ENUMS = {"announced", "pilot", "launched", "discontinued"}

def normalize_status(v: Any) -> str:
    """
    Normalize status values to standard enum: Announced, Pilot, Launched, Discontinued.
    
    Handles common variations:
    - "live", "prod", "production" → "Launched"
    - "beta", "pilot", "trial" → "Pilot"
    - "announce", "press release", "coming soon" → "Announced"
    - "end", "retire", "sunset", "closed" → "Discontinued"
    
    Args:
        v: Status value (any type, will be converted to string)
    
    Returns:
        str: Normalized status (capitalized) or original value if no match
    """
    s = str(v or "").strip()
    if not s:
        return ""
    low = s.lower()
    
    # Check for exact enum matches first
    for enum in _STATUS_ENUMS:
        if enum in low:
            return enum.capitalize()
    
    # Common synonyms/patterns
    if "live" in low or "prod" in low:
        return "Launched"
    if "beta" in low or "pilot" in low or "trial" in low:
        return "Pilot"
    if "announce" in low or "press" in low or "coming" in low:
        return "Announced"
    if "end" in low or "retire" in low or "sunset" in low or "closed" in low:
        return "Discontinued"
    
    # Unknown label, keep as-is
    return s


def normalize_fd(v: Any) -> str:
    """
    Normalize ternary (three-value) fields: True / False / Failed to disclose.
    
    Handles common variations:
    - "true", "yes", "y", "1" → "True"
    - "false", "no", "n", "0" → "False"
    - "unknown", "n/a", "not disclosed", "undisclosed" → "Failed to disclose"
    - Empty values → "Failed to disclose"
    
    Args:
        v: Value to normalize (any type)
    
    Returns:
        str: "True", "False", or "Failed to disclose"
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


# Regex pattern to match 4-digit years (1900-2099)
_year_re = re.compile(r"\b(19|20)\d{2}\b")

def normalize_year(v: Any, min_year: int = 2000, max_year: int = None) -> str:
    """
    Extract a 4-digit year (YYYY format) from a value with validation.
    
    Looks for years in a reasonable range. By default filters out:
    - Years before 2000 (likely unrelated company history)
    - Years after current year (future dates - invalid)
    
    Args:
        v: Value to extract year from (any type)
        min_year: Minimum valid year (default: 2000)
        max_year: Maximum valid year (default: current year)
    
    Returns:
        str: 4-digit year (YYYY) or empty string if not found or invalid
    
    Example:
        normalize_year("Launched in 2021") → "2021"
        normalize_year("2020-2023") → "2020" (first match)
        normalize_year("1993") → "" (too old, filtered out)
        normalize_year("2025") → "" (future date, filtered out if max_year=2024)
        normalize_year("No date") → ""
    """
    from datetime import datetime
    
    if max_year is None:
        max_year = datetime.now().year
    
    s = str(v or "").strip()
    if not s:
        return ""
    
    # Find all year matches in the string using finditer to get full match
    import re as re_module
    year_pattern = re_module.compile(r"\b(19|20)\d{2}\b")
    matches = year_pattern.finditer(s)
    
    for match in matches:
        year_str = match.group(0)  # Get full match (e.g., "2021")
        try:
            year_int = int(year_str)
            # Validate year is within reasonable range (filter out old unrelated dates and future dates)
            if min_year <= year_int <= max_year:
                return year_str
        except (ValueError, TypeError):
            continue
    # If no valid year in range, return empty string
    return ""


# -----------------------------
# Coercion into sheet headers
# -----------------------------

def _canonicalize_key(k: str) -> str:
    """
    Canonicalize a key for fuzzy matching.
    
    Converts to lowercase and collapses spaces/punctuation.
    This allows matching "Product Name" with "product_name" or "ProductName".
    
    Args:
        k: Key string to canonicalize
    
    Returns:
        str: Canonicalized key (lowercase, spaces collapsed)
    
    Example:
        _canonicalize_key("Product Name") → "product name"
        _canonicalize_key("product_name") → "product name"
        _canonicalize_key("ProductName!") → "productname"
    """
    k = (k or "").strip().lower()
    # Replace non-alphanumeric with single space
    k = re.sub(r"[^a-z0-9]+", " ", k)
    # Collapse multiple spaces into one
    k = re.sub(r"\s+", " ", k).strip()
    return k


def coerce_to_headers(
    obj: Dict[str, Any],
    headers: List[str],
    project_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map an LLM output dict to a row matching exact Excel headers.
    
    This function handles:
    1. Fuzzy matching: "product_name" matches "Product Name"
    2. Case-insensitive matching
    3. Evidence preservation: Keeps _evidence array if present
    4. Complete row: Ensures all headers exist (empty string if missing)
    5. Name override: Optionally forces name column to project_name
    
    Args:
        obj: Dictionary from LLM (may have different key names/format)
        headers: List of exact Excel column headers
        project_name: Optional project name to force into name column
    
    Returns:
        Dict[str, Any]: Row dict keyed by exact headers, with all headers present
    
    Example:
        obj = {"product_name": "Cheqd", "website_url": "https://..."}
        headers = ["Product Name", "Website"]
        Returns: {"Product Name": "Cheqd", "Website": "https://..."}
    """
    row: Dict[str, Any] = {}

    # Build a canonical lookup for the sheet headers
    # Maps "product name" → "Product Name" (exact header)
    header_canon: Dict[str, str] = {}
    for h in headers:
        header_canon[_canonicalize_key(h)] = h

    # First pass: attempt direct or canonical matches
    for k, v in (obj or {}).items():
        if k == "_evidence":
            # Evidence is handled separately below
            continue
        
        # Direct match (exact header name)
        if k in headers:
            row[k] = v
            continue
        
        # Canonical match (fuzzy matching)
        ck = _canonicalize_key(k)
        if ck in header_canon:
            row[header_canon[ck]] = v

    # Ensure all headers exist (fill missing ones with empty string)
    for h in headers:
        if h not in row:
            row[h] = ""

    # Evidence passthrough (preserve source tracking if LLM provided it)
    ev = obj.get("_evidence", [])
    if isinstance(ev, list):
        row["_evidence"] = ev
    else:
        row["_evidence"] = []

    # Force name column if requested (ensures consistency)
    if project_name:
        nh = name_header(headers)
        row[nh] = project_name

    return row
