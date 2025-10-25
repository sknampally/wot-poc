import pandas as pd
from pathlib import Path

def build_review_sheet(headers, recs, vals, out_path: Path):
    rows=[]
    for rec, val in zip(recs, vals):
        ev=rec.get("_evidence",[])
        field2src={}
        for e in ev:
            f=(e.get("field") or "").strip()
            if f and f not in field2src:
                field2src[f]=e.get("source_url","")
        name = ""
        # try to show a meaningful name
        for cand in ["Product Name","Project Name","Name","Initiative","Program"]:
            if cand in rec and str(rec.get(cand,"")).strip():
                name = rec.get(cand,""); break
        for h in headers:
            rows.append({
                "Project": name,
                "Field": h,
                "Value": rec.get(h,""),
                "Source": field2src.get(h,""),
                "ValidationIssues": "; ".join(val.get("issues",[]))
            })
    df=pd.DataFrame(rows,columns=["Project","Field","Value","Source","ValidationIssues"])
    # append/replace Review sheet within the same workbook
    with pd.ExcelWriter(out_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as xw:
        df.to_excel(xw, sheet_name="Review", index=False)
