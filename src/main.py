# src/main.py
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List, Dict, Any

from app import DATA_DIR, INPUT_XLSX, OUTPUT_XLSX
from app.utils.logger import setup_logging, get_logger
from app.config.codebook import load_codebook
from app.core.schema import load_headers, name_header
from app.core.export_excel import write_three_sheets
from app.workers.searcher import search_urls
from app.workers.scraper import scrape_urls
from app.workers.extractor import extract_record

log = get_logger("main")

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--targets", type=str, required=True, help='Comma or quote-separated list, e.g. "cheqd, esatus"')
    p.add_argument("--provider", type=str, default=os.getenv("LLM_PROVIDER", "openai"))
    p.add_argument("--model", type=str, default=os.getenv("LLM_MODEL", "gpt-4o-mini"))
    p.add_argument("--max-output-tokens", type=int, default=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "800")))
    return p.parse_args()

def main() -> None:
    logfile = setup_logging()  # set level from LOG_LEVEL (default INFO)
    log.info("Logging initialized at %s", logfile)

    args = parse_args()
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]

    log.info("input=%s output=%s", INPUT_XLSX, OUTPUT_XLSX)

    headers: List[str] = load_headers(Path(INPUT_XLSX))
    nm_col = name_header(headers)
    codebook = load_codebook()

    all_records: List[Dict[str, Any]] = []

    for project in targets:
        log.info("Processing %s", project)

        # --- SEARCH
        target_count = int(os.getenv("MAX_URLS_PER_PROJECT", "15"))
        url_items = search_urls(project, target_count=target_count)

        # --- SCRAPE
        pages = scrape_urls(project, url_items)

        # --- EXTRACT
        try:
            kwargs = {
                "provider": args.provider,
                "model": args.model,
                "max_output_tokens": args.max_output_tokens,
                "codebook": codebook,
            }
            rec = extract_record(
                project=project,
                headers=headers,
                pages=pages,
                **kwargs,
            )
        except Exception as e:
            log.exception("Unhandled error while extracting %s: %s", project, e)
            # still produce a seed row with just the name
            rec = {h: "" for h in headers}
            rec[nm_col] = project
            rec["_evidence"] = []

        all_records.append(rec)

    # --- WRITE EXCEL (append/update AI sheet, rebuild Comparison)
    log.info("Excel writing → %s", OUTPUT_XLSX)
    write_three_sheets(Path(INPUT_XLSX), headers, all_records, Path(OUTPUT_XLSX))
    log.info("Excel written with 3 sheets → %s", OUTPUT_XLSX)
    log.info("Done → %s", OUTPUT_XLSX)

if __name__ == "__main__":
    main()
