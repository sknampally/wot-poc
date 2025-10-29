# src/export_excel.py
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
import numpy as np

# ----------------- helpers -----------------

def _is_scalar(x: Any) -> bool:
    # Scalars we treat directly; everything else gets stringified
    return isinstance(x, (str, int, float, bool, type(None), np.generic))

def _to_text(x: Any) -> str:
    """Robust cell normalization → string for Excel output."""
    # Fast path: scalars
    if _is_scalar(x):
        # None/NaN to empty
        try:
            if x is None or (isinstance(x, float) and np.isnan(x)):
                return ""
        except Exception:
            pass
        # boolean/int/float to str
        return str(x)

    # pandas NA-like?
    try:
        if pd.isna(x):  # might raise when x is list/dict/ndarray
            return ""
    except Exception:
        pass

    # Lists/tuples/arrays → JSON-ish or joined strings if it looks like a list of URLs/strings
    if isinstance(x, (list, tuple, np.ndarray)):
        # If everything is scalar-ish, join by "; "
        try:
            items = list(x)
            if all(_is_scalar(i) for i in items):
                return "; ".join("" if i is None else str(i) for i in items if str(i) != "nan")
        except Exception:
            pass
        # Fallback to JSON
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)

    # Dicts → JSON
    if isinstance(x, dict):
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)

    # Everything else
    return str(x)

def _coerce_row_to_text(rec: Dict[str, Any], headers: List[str]) -> Dict[str, str]:
    """Return a new dict with all values rendered as strings, for a stable Excel write."""
    out: Dict[str, str] = {}
    for h in headers:
        out[h] = _to_text(rec.get(h, ""))
    return out

# ----------------- writers -----------------

def export_ai_sheet(headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    """
    Write the AI Data sheet: one row per record, columns in the same order as headers.
    If the workbook exists, we overwrite/replace the 'AI Data' sheet only.
    """
    # Build dataframe (stringified)
    rows = [_coerce_row_to_text(r, headers) for r in recs]
    ai_df = pd.DataFrame(rows, columns=headers)

    # Write/replace sheet
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Load existing workbook (preserve other sheets)
        with pd.ExcelWriter(output_xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
            ai_df.to_excel(w, sheet_name="AI Data", index=False)
    except FileNotFoundError:
        with pd.ExcelWriter(output_xlsx, engine="openpyxl") as w:
            ai_df.to_excel(w, sheet_name="AI Data", index=False)

def build_comparison_sheet(input_xlsx: Path, headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    """
    Build a side-by-side sheet:
      - Client Value (from the first sheet of input.xlsx)
      - AI Value (from our extracted records)
      - Match? (case-insensitive exact match after trimming)
    """
    # Load client sheet (first sheet)
    xls = pd.ExcelFile(input_xlsx)
    client_sheet = xls.sheet_names[0]
    client_df = pd.read_excel(input_xlsx, sheet_name=client_sheet)

    # Identify name column
    name_col = None
    for h in headers:
        if h.lower() in ("product name", "name", "project", "project name"):
            name_col = h
            break
    if not name_col:
        # fallback: use the first header as key
        name_col = headers[0]

    # Index client by name (string normalization)
    def _norm_key(s: Any) -> str:
        return _to_text(s).strip().lower()

    client_idx = { _norm_key(row.get(name_col, "")) : row for _, row in client_df.iterrows() }

    # Prepare AI dict by name
    ai_idx = { _norm_key(r.get(name_col, "")) : r for r in recs }

    # Build rows
    rows = []
    columns = ["Product Key", "Column", "Client Value", "AI Value", "Match?"]
    # Iterate over the union of keys so we see rows even if only client or only AI has them
    all_keys = sorted(set(client_idx.keys()).union(ai_idx.keys()))
    for k in all_keys:
        client_row = client_idx.get(k, {})
        ai_row = ai_idx.get(k, {})

        # For every header, compare values (stringified)
        for h in headers:
            client_v = _to_text(client_row.get(h, "")) if isinstance(client_row, dict) else _to_text(client_row[h]) if h in client_row else ""
            ai_v = _to_text(ai_row.get(h, ""))
            match = (client_v.strip().lower() == ai_v.strip().lower()) if client_v or ai_v else True  # both empty → treat as match
            rows.append({
                "Product Key": k,
                "Column": h,
                "Client Value": client_v,
                "AI Value": ai_v,
                "Match?": "✓" if match else "×"
            })

    cmp_df = pd.DataFrame(rows, columns=columns)

    # Write/replace sheet
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(output_xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
            cmp_df.to_excel(w, sheet_name="Comparison vs Client", index=False)
    except FileNotFoundError:
        with pd.ExcelWriter(output_xlsx, engine="openpyxl") as w:
            cmp_df.to_excel(w, sheet_name="Comparison vs Client", index=False)

# ----------------- (optional) light wrapper for legacy API -----------------

def export_records(headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    """Kept for compatibility if other code still calls export_records()."""
    export_ai_sheet(headers, recs, output_xlsx)
