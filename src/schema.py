import re, pandas as pd
from pathlib import Path

FD = ["True","False","Failed to disclose"]
STATUS_ENUM = ["Announced","Pilot","Launched","Discontinued"]
DLT_AVAIL_ENUM = [
    "Complete DLT Data",
    "Specifies DLT Technology but no DLT Instance",
    "Uses DLT, no specifications",
    "No DLT data at all"
]

LIKELY_NAME_HEADERS = ["Product Name", "Project Name", "Name", "Initiative", "Program"]

def load_headers(path: Path) -> list[str]:
    df = pd.read_excel(path, sheet_name=0, nrows=0)
    return list(df.columns)

def _name_header(headers: list[str]) -> str:
    lower = {h.lower(): h for h in headers}
    for cand in LIKELY_NAME_HEADERS:
        if cand.lower() in lower:
            return lower[cand.lower()]
    # fallback to first column
    return headers[0] if headers else "Product Name"

def normalize_status(v): 
    if not v: return None
    v=v.strip().title()
    for opt in STATUS_ENUM:
        if v.startswith(opt): return opt
    return v

def normalize_fd(v):
    if not v: return None
    v=v.strip().lower()
    if v in ["true","yes","1","y"]: return "True"
    if v in ["false","no","0","n"]: return "False"
    return "Failed to disclose"

def normalize_year(v):
    if v is None: return None
    s=str(v); m=re.search(r"(19|20)\d{2}",s)
    return m.group(0) if m else None
