# src/app/core/export_excel.py
from __future__ import annotations
import logging
from typing import List, Dict, Any
from pathlib import Path
import pandas as pd
from openpyxl import load_workbook, Workbook

from app.core.schema import name_header

log = logging.getLogger("export_excel")

def _load_input_df(path: Path) -> pd.DataFrame:
    if not path.exists():
        # create an empty input sheet with a minimal schema
        df = pd.DataFrame(columns=["Product Name"])
        with pd.ExcelWriter(path, engine="openpyxl", mode="w") as xw:
            df.to_excel(xw, sheet_name="Input", index=False)
    return pd.read_excel(path, sheet_name=0)

def _ensure_book_with_three_sheets(out_path: Path) -> None:
    if out_path.exists(): return
    wb = Workbook()
    # Create exactly 3 sheets in order
    ws1 = wb.active; ws1.title = "Input"
    wb.create_sheet("AI Data")
    wb.create_sheet("Comparison")
    wb.save(out_path)

def _upsert_ai_rows(out_path: Path, headers: List[str], records: List[Dict[str, Any]]) -> None:
    _ensure_book_with_three_sheets(out_path)

    with pd.ExcelWriter(out_path, engine="openpyxl", mode="a", if_sheet_exists="overlay") as xw:
        try:
            ai_df = pd.read_excel(out_path, sheet_name="AI Data")
        except Exception:
            ai_df = pd.DataFrame(columns=headers)
        ncol = name_header(headers)
        # Upsert by name column
        for rec in records:
            row = {h: rec.get(h, "") for h in headers}
            if ai_df.empty:
                ai_df = pd.DataFrame([row], columns=headers)
            else:
                mask = (ai_df[ncol].astype(str).str.strip().str.lower()
                        == str(row.get(ncol, "")).strip().lower())
                if mask.any():
                    ai_df.loc[mask, headers] = [row.get(h, "") for h in headers]
                else:
                    ai_df = pd.concat([ai_df, pd.DataFrame([row], columns=headers)], ignore_index=True)
        ai_df.to_excel(xw, sheet_name="AI Data", index=False)

def _build_comparison(out_path: Path, headers: List[str], input_df: pd.DataFrame) -> None:
    try:
        ai_df = pd.read_excel(out_path, sheet_name="AI Data")
    except Exception:
        ai_df = pd.DataFrame(columns=headers)

    ncol = name_header(headers)
    if ncol not in input_df.columns:
        input_df[ncol] = ""

    # Outer merge on name column
    merged = input_df.merge(ai_df, how="outer", on=ncol, suffixes=("_Client", "_AI"))

    rows = []
    for _, r in merged.iterrows():
        proj = str(r.get(ncol, ""))
        for h in headers:
            client_val = r.get(f"{h}_Client", "")
            ai_val     = r.get(f"{h}_AI", "")
            # Treat NaNs
            client_str = "" if pd.isna(client_val) else str(client_val)
            ai_str     = "" if pd.isna(ai_val) else str(ai_val)

            if client_str and ai_str:
                match = (client_str.strip().lower() == ai_str.strip().lower())
                status = "Match" if match else "Different"
            elif client_str or ai_str:
                status = "Only Client" if client_str else "Only AI"
            else:
                status = "No data"

            rows.append({
                "Project": proj,
                "Field": h,
                "Client Value": client_str,
                "AI Value": ai_str,
                "Status": status
            })
    comp = pd.DataFrame(rows, columns=["Project","Field","Client Value","AI Value","Status"])
    with pd.ExcelWriter(out_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as xw:
        comp.to_excel(xw, sheet_name="Comparison", index=False)

def write_three_sheets(input_xlsx: Path, headers: List[str], records: List[Dict[str, Any]], output_xlsx: Path) -> None:
    log.info("Excel writing → %s", output_xlsx)
    _ensure_book_with_three_sheets(output_xlsx)

    # Always (re)write Input from input.xlsx (sheet 0) into "Input"
    input_df = _load_input_df(input_xlsx)
    with pd.ExcelWriter(output_xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace") as xw:
        input_df.to_excel(xw, sheet_name="Input", index=False)

    # AI Data upsert
    _upsert_ai_rows(output_xlsx, headers, records)

    # Comparison
    _build_comparison(output_xlsx, headers, input_df)
    log.info("Excel written with 3 sheets → %s", output_xlsx)
