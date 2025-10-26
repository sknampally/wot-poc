import argparse, json, os
from pathlib import Path
import pandas as pd

from schema import load_headers, _name_header
from searcher import search_project
from scraper import scrape_urls
from extractor import extract_record
from validator import validate_record
from export_excel import export_ai_sheet, build_comparison_sheet
from review_report import build_review_sheet
from include_check import inclusion_decision

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
INPUT_XLSX = DATA_DIR / "input.xlsx"
OUTPUT_XLSX = DATA_DIR / "output.xlsx"

def detect_targets_from_input(input_path: Path, mode: str = "auto", limit: int = 4) -> list[str]:
    xls = pd.ExcelFile(input_path)
    sheet = xls.sheet_names[0]
    df = pd.read_excel(input_path, sheet_name=sheet)
    headers = list(df.columns)
    name_col = _name_header(headers)

    # All project names that are non-empty
    names = df[name_col].fillna("").astype(str).str.strip()
    non_empty = names[names != ""]

    if mode == "all":
        return list(dict.fromkeys(non_empty.tolist()))  # preserve order, unique

    # auto: pick emptiest rows
    df_wo_name = df.drop(columns=[name_col], errors="ignore")
    empties = df_wo_name.isna() | (df_wo_name.astype(str).apply(lambda s: s.str.strip()==""))
    empty_counts = empties.sum(axis=1)
    candidates = df[names != ""].copy()
    candidates["empty_count"] = empty_counts[names != ""]
    ranking = candidates.sort_values(by="empty_count", ascending=False)
    targets = []
    for n in ranking[name_col].astype(str).str.strip().tolist():
        if n and n not in targets:
            targets.append(n)
        if len(targets) >= limit:
            break
    return targets

def run_for_project(name: str, headers: list[str]) -> tuple[dict, dict]:
    proj_dir = DATA_DIR / "cache" / name.replace(" ", "_")
    urls = search_project(name, proj_dir)
    pages = scrape_urls(urls, proj_dir)
    rec = extract_record(name, headers, pages)
    rec["_inclusion"] = inclusion_decision(rec)
    val = validate_record(rec)
    (proj_dir).mkdir(parents=True, exist_ok=True)
    (proj_dir / "record_debug.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    filled = sum(1 for k,v in rec.items() if k!='_evidence' and isinstance(v,str) and v.strip())
    print(f"[run] {name}: fields filled -> {filled}")
    return rec, val

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--targets", type=str, required=True, help='"auto", "all", or comma-separated names')
    p.add_argument("--limit", type=int, default=0, help="Max projects to process (0 = no limit)")

    # Runtime LLM overrides (CLI > .env)
    p.add_argument("--provider", type=str, default=None, choices=["openai","ollama"], help="LLM provider (overrides .env)")
    p.add_argument("--model", type=str, default=None, help="LLM model name (overrides .env)")
    p.add_argument("--temperature", type=float, default=None, help="LLM temperature (overrides .env)")
    p.add_argument("--max-output-tokens", type=int, default=None, help="LLM max output tokens (overrides .env)")

    args = p.parse_args()

    # Apply runtime overrides via env for extractor.py to read
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        # Route to the right env var by provider if specified, else set both
        if args.provider == "openai":
            os.environ["OPENAI_MODEL"] = args.model
        elif args.provider == "ollama":
            os.environ["OLLAMA_MODEL"] = args.model
        else:
            os.environ["OPENAI_MODEL"] = args.model
            os.environ["OLLAMA_MODEL"] = args.model
    if args.temperature is not None:
        os.environ["OPENAI_TEMPERATURE"] = str(args.temperature)
    if args.max_output_tokens is not None:
        os.environ["OPENAI_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)

    headers = load_headers(INPUT_XLSX)

    t = args.targets.strip().lower()
    if t == "all":
        targets = detect_targets_from_input(INPUT_XLSX, mode="all")
        print("[all] detected targets:", targets)
    elif t == "auto":
        targets = detect_targets_from_input(INPUT_XLSX, mode="auto", limit=4)
        print("[auto] detected targets:", targets)
    else:
        targets = [x.strip() for x in args.targets.split(",") if x.strip()]

    if args.limit and len(targets) > args.limit:
        targets = targets[:args.limit]
        print(f"[limit] processing first {args.limit} targets")

    recs, vals = [], []
    for name in targets:
        print("Processing", name)
        r, v = run_for_project(name, headers)
        recs.append(r); vals.append(v)

    # 1) AI Data
    export_ai_sheet(headers, recs, OUTPUT_XLSX)
    # 2) Comparison vs Client
    build_comparison_sheet(INPUT_XLSX, headers, recs, OUTPUT_XLSX)
    # 3) Review (evidence & validation)
    build_review_sheet(headers, recs, vals, OUTPUT_XLSX)

    print(f"Done → {OUTPUT_XLSX}")

if __name__ == "__main__":
    main()
