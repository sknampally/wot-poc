import pandas as pd
from pathlib import Path
from schema import _name_header

def _ordered_rows(headers: list[str], records: list[dict]) -> pd.DataFrame:
    # Ensure everything is string/object to avoid dtype conflicts in Excel
    safe_rows = []
    for r in records:
        safe = {h: ("" if r.get(h) is None else str(r.get(h))) for h in headers}
        safe_rows.append(safe)
    return pd.DataFrame(safe_rows, columns=headers, dtype="object")

def export_ai_sheet(headers: list[str], records: list[dict], out_path: Path):
    df_ai = _ordered_rows(headers, records)
    with pd.ExcelWriter(out_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as xw:
        df_ai.to_excel(xw, sheet_name="AI_Data", index=False)

def build_comparison_sheet(input_path: Path, headers: list[str], records: list[dict], out_path: Path):
    xls = pd.ExcelFile(input_path)
    sheet_name = xls.sheet_names[0]
    df_client = pd.read_excel(input_path, sheet_name=sheet_name)

    name_col = _name_header(headers)

    # Normalize for comparison (stringify, strip). Keep original client types for display.
    def _norm(v): 
        if pd.isna(v): return ""
        return str(v).strip()

    client_idx = { _norm(n): i for i, n in enumerate(df_client[name_col].astype(str)) }
    rows = []

    for rec in records:
        proj_name = _norm(rec.get(name_col, ""))
        client_row = df_client.iloc[client_idx[proj_name]] if proj_name in client_idx else None

        for h in headers:
            ai_val = _norm(rec.get(h, ""))
            client_val = _norm(client_row[h]) if (client_row is not None and h in df_client.columns) else ""
            match = (ai_val == client_val) and (ai_val != "" or client_val != "")
            rows.append({
                "Project": proj_name,
                "Field": h,
                "Client Value": client_val,
                "AI Value": ai_val,
                "Match": "✅" if match else ("⬜️" if (ai_val=="" and client_val=="") else "❌")
            })

    df_cmp = pd.DataFrame(rows, columns=["Project","Field","Client Value","AI Value","Match"])
    with pd.ExcelWriter(out_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as xw:
        df_cmp.to_excel(xw, sheet_name="Comparison", index=False)
