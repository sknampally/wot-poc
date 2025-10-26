import pandas as pd
from pathlib import Path
from schema import _name_header

def _ensure_object_df(headers, records):
    rows = []
    for r in records:
        rows.append({h: ("" if r.get(h) is None else str(r.get(h))) for h in headers})
    return pd.DataFrame(rows, columns=headers, dtype="object")

def _to_writer(path: Path, mode: str):
    # If file doesn't exist, force write mode
    if mode == "a" and not path.exists():
        mode = "w"
    return pd.ExcelWriter(path, engine="openpyxl", mode=mode, if_sheet_exists="replace")

def export_ai_sheet(headers, records, out_path: Path):
    df_ai = _ensure_object_df(headers, records)
    with _to_writer(out_path, "a") as xw:
        df_ai.to_excel(xw, sheet_name="AI_Data", index=False)

def build_comparison_sheet(input_path: Path, headers, records, out_path: Path):
    xls = pd.ExcelFile(input_path)
    sheet_name = xls.sheet_names[0]
    df_client = pd.read_excel(input_path, sheet_name=sheet_name)
    name_col = _name_header(headers)

    def _norm(v): 
        import pandas as pd
        if pd.isna(v): return ""
        return str(v).strip()

    client_idx = { _norm(n): i for i, n in enumerate(df_client[name_col].astype(str)) }
    rows = []
    for rec in records:
        proj = _norm(rec.get(name_col, ""))
        crow = df_client.iloc[client_idx[proj]] if proj in client_idx else None
        for h in headers:
            ai = _norm(rec.get(h, ""))
            cv = _norm(crow[h]) if (crow is not None and h in df_client.columns) else ""
            match = (ai == cv) and (ai != "" or cv != "")
            rows.append({
                "Project": proj,
                "Field": h,
                "Client Value": cv,
                "AI Value": ai,
                "Match": "✅" if match else ("⬜️" if (ai=="" and cv=="") else "❌"),
            })
    df_cmp = pd.DataFrame(rows, columns=["Project","Field","Client Value","AI Value","Match"])
    with _to_writer(out_path, "a") as xw:
        df_cmp.to_excel(xw, sheet_name="Comparison", index=False)
