# src/export_excel.py
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
import numpy as np
from openpyxl import Workbook
import pandas as pd
from pathlib import Path
from typing import Any, Dict, List

def _to_text(v: Any) -> str:
    if v is None: return ""
    if isinstance(v, float) and pd.isna(v): return ""
    if isinstance(v, (list, tuple, set)): return ", ".join(map(_to_text, v))
    return str(v)

def _coerce_row(rec: Dict[str, Any], headers: List[str]) -> Dict[str, Any]:
    out = {}
    for h in headers:
        v = rec.get(h, "")
        out[h] = _to_text(v)
    return out

def _is_scalar(x: Any) -> bool:
    return isinstance(x, (str, int, float, bool, type(None), np.generic))

def _to_text(x: Any) -> str:
    if _is_scalar(x):
        try:
            if x is None or (isinstance(x, float) and np.isnan(x)):
                return ""
        except Exception:
            pass
        return str(x)
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    if isinstance(x, (list, tuple, np.ndarray)):
        try:
            it = list(x)
            if all(_is_scalar(i) for i in it):
                return "; ".join("" if i is None else str(i) for i in it if str(i) != "nan")
        except Exception:
            pass
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)
    if isinstance(x, dict):
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)
    return str(x)

def _coerce_row(rec: Dict[str, Any], headers: List[str]) -> Dict[str, str]:
    return {h: _to_text(rec.get(h, "")) for h in headers}

def export_ai_sheet(headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    rows = [_coerce_row(r, headers) for r in recs]
    df = pd.DataFrame(rows, columns=headers)
    with pd.ExcelWriter(output_xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        df.to_excel(w, sheet_name="AI Data", index=False)

def build_comparison_sheet(input_xlsx: Path, headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    xls = pd.ExcelFile(input_xlsx)
    client_sheet = xls.sheet_names[0]
    client_df = pd.read_excel(input_xlsx, sheet_name=client_sheet)

    # Name column
    name_col = None
    for h in headers:
        if h.lower() in ("product name","name","project","project name"):
            name_col = h
            break
    if not name_col:
        name_col = headers[0]

    def _key(s): return _to_text(s).strip().lower()
    client_idx = {_key(row.get(name_col, "")): row for _, row in client_df.iterrows()}
    ai_idx     = {_key(r.get(name_col, "")): r   for r in recs}

    rows = []
    columns = ["Product Key","Column","Client Value","AI Value","Match?"]
    all_keys = sorted(set(client_idx.keys()).union(ai_idx.keys()))
    for k in all_keys:
        c_row = client_idx.get(k, {})
        a_row = ai_idx.get(k, {})
        for h in headers:
            cv = _to_text(c_row.get(h, "")) if isinstance(c_row, dict) else ""
            av = _to_text(a_row.get(h, ""))
            match = (cv.strip().lower() == av.strip().lower()) if (cv or av) else True
            rows.append({
                "Product Key": k,
                "Column": h,
                "Client Value": cv,
                "AI Value": av,
                "Match?": "✓" if match else "×"
            })
    cmp_df = pd.DataFrame(rows, columns=columns)

    with pd.ExcelWriter(output_xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        cmp_df.to_excel(w, sheet_name="Comparison vs Client", index=False)

def write_fresh_workbook_with_three_sheets(input_xlsx: Path, headers: List[str], recs: List[Dict[str, Any]], output_xlsx: Path) -> None:
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)

    xls = pd.ExcelFile(input_xlsx)
    client_sheet = xls.sheet_names[0]
    client_df = pd.read_excel(input_xlsx, sheet_name=client_sheet)

    ai_rows = [_coerce_row(r, headers) for r in recs]
    ai_df = pd.DataFrame(ai_rows, columns=headers)

    # comparison
    name_col = None
    for h in headers:
        if h.lower() in ("product name", "name", "project", "project name"):
            name_col = h; break
    if not name_col:
        name_col = headers[0]

    def _key(s): return _to_text(s).strip().lower()
    client_idx = {_key(row.get(name_col, "")): row for _, row in client_df.iterrows()}
    ai_idx     = {_key(r.get(name_col, "")): r   for r in recs}

    rows = []
    columns = ["Product Key","Column","Client Value","AI Value","Match?"]
    all_keys = sorted(set(client_idx.keys()).union(ai_idx.keys()))
    for k in all_keys:
        c_row = client_idx.get(k, {})
        a_row = ai_idx.get(k, {})
        for h in headers:
            cv = _to_text(c_row.get(h, "")) if isinstance(c_row, dict) else ""
            av = _to_text(a_row.get(h, ""))
            match = (cv.strip().lower() == av.strip().lower()) if (cv or av) else True
            rows.append({"Product Key": k, "Column": h, "Client Value": cv, "AI Value": av, "Match?": "✓" if match else "×"})
    cmp_df = pd.DataFrame(rows, columns=columns)

    with pd.ExcelWriter(output_xlsx, engine="openpyxl", mode="w") as w:
        client_df.to_excel(w, sheet_name="Input (Client)", index=False)
        ai_df.to_excel(w,      sheet_name="AI Data",       index=False)
        cmp_df.to_excel(w,     sheet_name="Comparison vs Client", index=False)