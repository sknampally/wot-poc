# src/app/core/export_excel.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.core.schema import name_header

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

def _build_comparison(df_input: pd.DataFrame, df_ai: pd.DataFrame, headers: List[str]) -> pd.DataFrame:
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
            client_val = _norm(rec.get(f"{h}_client", ""))
            ai_val = _norm(rec.get(f"{h}_ai", ""))
            # Only count as match if BOTH sides non-empty and equal (case-insensitive)
            match = (client_val != "" and ai_val != "" and client_val.strip().lower() == ai_val.strip().lower())
            rows.append({
                "Project": project,
                "Field": h,
                "Client Value": client_val,
                "AI Value": ai_val,
                "Match?": "✓" if match else "",
            })
    return pd.DataFrame(rows, columns=["Project", "Field", "Client Value", "AI Value", "Match?"])

def write_three_sheets(input_xlsx: Path, headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    df_input = _load_input_df(input_xlsx)
    df_new_ai = _build_ai_df(headers, recs)
    df_ai = _append_or_update_ai_sheet(output_xlsx, headers, df_new_ai)
    df_cmp = _build_comparison(df_input, df_ai, headers)

    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as xw:
        df_input.to_excel(xw, sheet_name="Input", index=False)
        df_ai.to_excel(xw, sheet_name="AI", index=False)
        df_cmp.to_excel(xw, sheet_name="Comparison", index=False)
