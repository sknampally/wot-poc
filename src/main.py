# src/main.py
import argparse, json, os
from pathlib import Path
import pandas as pd

# our modules
from schema import load_headers, _name_header
from searcher import search_project
from scraper import scrape_urls
from extractor import extract_record
from validator import validate_record
from export_excel import export_ai_sheet, build_comparison_sheet
from review_report import build_review_sheet
from include_check import inclusion_decision

import extractor as extractor_mod  # to wire CLI into extractor globals

# Paths
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INPUT_XLSX = DATA_DIR / "input.xlsx"
OUTPUT_XLSX = DATA_DIR / "output.xlsx"


def detect_targets_from_input(input_path: Path, mode: str = "auto", limit: int = 4) -> list[str]:
    xls = pd.ExcelFile(input_path)
    sheet = xls.sheet_names[0]
    df = pd.read_excel(input_path, sheet_name=sheet)
    headers = list(df.columns)
    name_col = _name_header(headers)

    names = df[name_col].fillna("").astype(str).str.strip()
    non_empty = names[names != ""]

    if mode == "all":
        # unique, preserve order
        return list(dict.fromkeys(non_empty.tolist()))

    # auto: choose emptiest rows first
    df2 = df.copy()
    if name_col in df2.columns:
        df2 = df2.drop(columns=[name_col])
    empties = df2.isna() | (df2.astype(str).apply(lambda s: s.str.strip() == ""))
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
    proj_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {name}")

    urls = search_project(name, proj_dir)
    pages = scrape_urls(urls, proj_dir)
    rec = extract_record(name, headers, pages)

    # project-specific extras
    rec["_inclusion"] = inclusion_decision(rec)

    # validate
    val = validate_record(rec)

    # debug dump
    (proj_dir / "record_debug.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    filled = sum(
        1 for k, v in rec.items()
        if k != "_evidence" and isinstance(v, str) and v.strip()
    )
    print(f"[run] {name}: fields filled -> {filled}")
    return rec, val


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--targets", type=str, required=True,
                   help='"auto", "all", or comma-separated project names')
    p.add_argument("--provider", type=str, choices=["openai", "ollama"], default=None)
    p.add_argument("--model", type=str, default=None)
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--max-output-tokens", type=int, dest="max_output_tokens", default=None)
    args = p.parse_args()

    # ---- Wire CLI -> env -> extractor module (authoritative in-process) ----
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
        extractor_mod.PROVIDER = args.provider.lower()
    if args.model:
        os.environ["OPENAI_MODEL"] = args.model   # if provider=openai
        os.environ["OLLAMA_MODEL"] = args.model   # if provider=ollama
        extractor_mod.OPENAI_MODEL = args.model
        extractor_mod.OLLAMA_MODEL = args.model
    if args.temperature is not None:
        os.environ["OPENAI_TEMPERATURE"] = str(args.temperature)
        extractor_mod.OPENAI_TEMPERATURE = args.temperature
    if args.max_output_tokens is not None:
        os.environ["OPENAI_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)
        extractor_mod.OPENAI_MAX_TOKENS = args.max_output_tokens

    print(f"[config] LLM provider resolved → {extractor_mod.PROVIDER}, "
          f"model → {(extractor_mod.OPENAI_MODEL if extractor_mod.PROVIDER=='openai' else extractor_mod.OLLAMA_MODEL)}")

    # ---- Pre-flight checks ----
    if not INPUT_XLSX.exists():
        print(f"[error] Missing input file: {INPUT_XLSX}")
        return

    headers = load_headers(INPUT_XLSX)
    if not headers:
        print("[error] Could not load headers from input.xlsx")
        return

    # ---- Resolve targets ----
    t = (args.targets or "").strip().lower()
    if t == "all":
        targets = detect_targets_from_input(INPUT_XLSX, mode="all")
        print("[all] detected targets:", targets)
    elif t == "auto":
        targets = detect_targets_from_input(INPUT_XLSX, mode="auto", limit=4)
        print("[auto] detected targets:", targets)
    else:
        # explicit list
        targets = [x.strip() for x in (args.targets or "").split(",") if x.strip()]
        if not targets:
            print("[error] No targets specified.")
            return

    # ---- Run pipeline ----
    recs, vals = [], []
    for name in targets:
        try:
            r, v = run_for_project(name, headers)
            recs.append(r)
            vals.append(v)
        except Exception as e:
            print(f"[error] {name}: {e}")

    # ---- Write outputs ----
    if recs:
        export_ai_sheet(headers, recs, OUTPUT_XLSX)                   # AI_Data sheet
        build_comparison_sheet(INPUT_XLSX, headers, recs, OUTPUT_XLSX)  # Comparison sheet
        build_review_sheet(headers, recs, vals, OUTPUT_XLSX)            # Review sheet
        print(f"Done → {OUTPUT_XLSX}")
    else:
        print("[warn] No records generated; nothing to write.")


if __name__ == "__main__":
    main()
