"""
Excel export module with comparison and matching logic.

This module handles:
1. Loading input data from input.xlsx
2. Building AI-extracted data DataFrame
3. Creating comparison sheet (side-by-side AI vs manual data)
4. Semantic similarity matching for long text fields
5. Normalization of values for accurate comparison
6. Excluding source fields from matching (only Data Columns are compared)

The comparison uses fuzzy matching (60% similarity threshold) for long text
fields to account for variations in phrasing while requiring exact matches
for short fields (URLs, dates, IDs).
"""
# src/app/core/export_excel.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.core.schema import name_header, normalize_status, normalize_fd, normalize_year

def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    s = str(v).strip()
    if s.lower() in {"nan", "none", "null"}:
        return ""
    return s

def _load_input_df(input_xlsx: Path) -> pd.DataFrame:
    return pd.read_excel(input_xlsx, sheet_name=0, dtype=str).fillna("")

def _build_ai_df(headers: List[str], recs: List[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for r in recs:
        row = {h: _norm(r.get(h, "")) for h in headers}
        # evidence: keep as JSON string for now
        ev = r.get("_evidence", [])
        row["_evidence"] = "" if not ev else str(ev)
        rows.append(row)
    df = pd.DataFrame(rows, columns=headers + ["_evidence"])
    return df

def _append_or_update_ai_sheet(output_xlsx: Path, headers: List[str], df_new: pd.DataFrame) -> pd.DataFrame:
    nm_col = name_header(headers)
    if output_xlsx.exists():
        try:
            book = load_workbook(output_xlsx)
            if "AI" in book.sheetnames:
                old_df = pd.read_excel(output_xlsx, sheet_name="AI", dtype=str).fillna("")
            else:
                old_df = pd.DataFrame(columns=headers + ["_evidence"])
        except InvalidFileException:
            old_df = pd.DataFrame(columns=headers + ["_evidence"])
    else:
        old_df = pd.DataFrame(columns=headers + ["_evidence"])

    if old_df.empty:
        return df_new

    old_df.set_index(nm_col, inplace=True, drop=False)
    df_new.set_index(nm_col, inplace=True, drop=False)

    # update existing rows where new has non-empty values, append new projects
    merged = old_df.combine_first(df_new)
    for idx, row in df_new.iterrows():
        if idx in merged.index:
            for c in headers + ["_evidence"]:
                nv = _norm(row.get(c, ""))
                if nv:
                    merged.at[idx, c] = nv

    merged.reset_index(drop=True, inplace=True)
    # ensure col order
    merged = merged.reindex(columns=headers + ["_evidence"])
    return merged

def _text_similarity(str1: str, str2: str) -> float:
    """Calculate similarity ratio between two strings (0.0 to 1.0)."""
    if not str1 or not str2:
        return 0.0
    from difflib import SequenceMatcher
    return SequenceMatcher(None, str1.lower().strip(), str2.lower().strip()).ratio()

def _is_text_match(client_val: str, ai_val: str, field_name: str, threshold: float = 0.6) -> bool:
    """
    Check if two text values match, using semantic similarity for long text fields.
    
    Strategy:
    - Short fields (URLs, dates, IDs): Require exact match
    - Long text fields (>50 chars): Use similarity threshold (default 60%)
    - Also considers word overlap for better semantic matching
    
    Args:
        client_val: Manual/client-provided value
        ai_val: AI-extracted value
        field_name: Field name (used to determine matching strategy)
        threshold: Similarity threshold for long fields (0.0 to 1.0)
    
    Returns:
        bool: True if values match (exact or semantically similar)
    """
    client_clean = str(client_val).strip() if pd.notna(client_val) else ""
    ai_clean = str(ai_val).strip() if pd.notna(ai_val) else ""
    
    if not client_clean or not ai_clean:
        return client_clean == ai_clean
    
    if client_clean.lower() == ai_clean.lower():
        return True
    
    # For short fields, require exact match
    if any(x in field_name.lower() for x in ['date', 'url', 'website', 'repository', 'source', 'id']):
        return False
    
    # For long text fields, use similarity threshold
    if len(client_clean) > 50 or len(ai_clean) > 50:
        similarity = _text_similarity(client_clean, ai_clean)
        # Focus on significant words (4+ chars) for better matching
        client_words = set(word for word in client_clean.lower().split() if len(word) > 3)
        ai_words = set(word for word in ai_clean.lower().split() if len(word) > 3)
        if len(client_words) > 0 and len(ai_words) > 0:
            word_overlap = len(client_words & ai_words) / len(client_words | ai_words) if len(client_words | ai_words) > 0 else 0
            significant_overlap = word_overlap * 1.3  # Boost significant word overlap
            final_score = max(similarity, significant_overlap)
            return final_score >= threshold
    
    return False

def _build_comparison(df_input: pd.DataFrame, df_ai: pd.DataFrame, headers: List[str]) -> pd.DataFrame:
    """
    Build comparison sheet showing AI vs manual data side-by-side.
    
    Process:
    1. Merge input and AI data on project name
    2. For each field:
       - Normalize values based on field type (status, year, ternary)
       - Use semantic similarity matching for text fields
       - Skip source fields (Live Source, Archived Source)
    3. Mark matches with ✓ symbol
    
    Args:
        df_input: DataFrame with manual/client data
        df_ai: DataFrame with AI-extracted data
        headers: List of field names to compare
    
    Returns:
        pd.DataFrame: Comparison sheet with columns:
            - Project: Project name
            - Field: Field name
            - Client Value: Manual value
            - AI Value: AI-extracted value
            - Match?: ✓ if match, empty if not
    """
    nm_col = name_header(headers)

    left = df_input.copy()
    left[nm_col] = left[nm_col].apply(_norm)

    right = df_ai.copy()
    right[nm_col] = right[nm_col].apply(_norm)

    # strict inner merge on project name to compare like-for-like
    merged = left.merge(right, on=nm_col, how="inner", suffixes=("_client", "_ai"))

    rows: List[Dict[str, Any]] = []
    for _, rec in merged.iterrows():
        project = _norm(rec[nm_col])
        for h in headers:
            # Skip source fields from comparison (they're not data columns)
            if "Live Source" in h or "Archived Source" in h:
                continue
            
            client_val = _norm(rec.get(f"{h}_client", ""))
            ai_val = _norm(rec.get(f"{h}_ai", ""))
            
            # Normalize both values for comparison based on field type
            # Status fields
            if "Status" in h and "Source" not in h:
                client_val = normalize_status(client_val)
                ai_val = normalize_status(ai_val)
            # Year fields
            elif any(year_field in h for year_field in ["Announcement", "Launch"]) and "Source" not in h:
                client_val = normalize_year(client_val)
                ai_val = normalize_year(ai_val)
            # Ternary fields
            elif any(ternary in h for ternary in ["Uses", "Has", "Targets", "Politically", "Does it use", "Endorses"]) and "Source" not in h:
                client_val = normalize_fd(client_val)
                ai_val = normalize_fd(ai_val)
            
            # URL normalization: remove trailing slashes and compare
            if "Website" in h or "URL" in h or "repository" in h.lower():
                client_val = client_val.rstrip('/')
                ai_val = ai_val.rstrip('/')
            
            # Use semantic similarity matching for text fields
            match = _is_text_match(client_val, ai_val, h)
            rows.append({
                "Project": project,
                "Field": h,
                "Client Value": client_val,
                "AI Value": ai_val,
                "Match?": "✓" if match else "",
            })
    return pd.DataFrame(rows, columns=["Project", "Field", "Client Value", "AI Value", "Match?"])

def export_to_excel(input_xlsx: Path, headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    """
    Export extraction results to Excel with Input, AI, and Comparison sheets.
    
    Creates/updates output.xlsx with:
    - Input sheet: Original data from input.xlsx
    - AI sheet: AI-extracted data (updates existing if file exists)
    - Comparison sheet: Side-by-side comparison of AI vs manual data
    
    Args:
        input_xlsx: Path to input Excel file
        headers: List of column headers
        recs: List of extracted records (one per project)
        output_xlsx: Path to output Excel file
    """
    df_input = _load_input_df(input_xlsx)
    df_new_ai = _build_ai_df(headers, recs)
    df_ai = _append_or_update_ai_sheet(output_xlsx, headers, df_new_ai)
    df_cmp = _build_comparison(df_input, df_ai, headers)

    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as xw:
        df_input.to_excel(xw, sheet_name="Input", index=False)
        df_ai.to_excel(xw, sheet_name="AI", index=False)
        df_cmp.to_excel(xw, sheet_name="Comparison", index=False)
