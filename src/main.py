# src/main.py
from __future__ import annotations

import os
import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Any

# 1) Ensure repo root on sys.path (so "app.*" imports work when run directly)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 2) Lightweight .env loader (no hard dep, optional)
def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        env_path = ROOT.parent / ".env" if (ROOT.name == "src") else ROOT / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
    except Exception:
        # Silently ignore if python-dotenv is missing; env vars can still be provided by shell
        pass

_load_dotenv_if_present()

# 3) Regular imports from our package
from app.utils.logger import setup_logging, get_logger
from app.core.schema import load_headers
from app.core.export_excel import write_three_sheets
from app.workers.searcher import search_urls
from app.workers.scraper import scrape_urls
from app.workers.extractor import extract_record

log = logging.getLogger("main")


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WOT-POC: scrape → extract → export (3-sheet Excel)"
    )
    p.add_argument(
        "--targets",
        required=True,
        help='Comma-separated project names, e.g. "cheqd,Trusted Biz"',
    )
    p.add_argument(
        "--provider",
        default=os.getenv("LLM_PROVIDER", "openai"),
        help="LLM provider (only 'openai' is wired here).",
    )
    p.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        help="OpenAI model name (e.g., gpt-4o-mini).",
    )
    p.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "800")),
        help="Max tokens for the LLM response.",
    )
    p.add_argument(
        "--input",
        default=str((ROOT.parent / "data" / "input.xlsx") if ROOT.name == "src" else (ROOT / "data" / "input.xlsx")),
        help="Path to input.xlsx (client sheet).",
    )
    p.add_argument(
        "--output",
        default=str((ROOT.parent / "data" / "output.xlsx") if ROOT.name == "src" else (ROOT / "data" / "output.xlsx")),
        help="Path to output.xlsx (will be created/updated).",
    )
    return p.parse_args(argv)


def ensure_paths() -> Dict[str, Path]:
    # Resolve common paths relative to repo root
    repo = ROOT.parent if ROOT.name == "src" else ROOT
    data_dir = repo / "data"
    cache_dir = data_dir / "cache"
    logs_dir = repo / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return {
        "repo": repo,
        "data": data_dir,
        "cache": cache_dir,
        "logs": logs_dir,
        "input_xlsx": data_dir / "input.xlsx",
        "output_xlsx": data_dir / "output.xlsx",
    }


def main(argv: List[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    # Set up logging once, file at <repo>/logs/wot.log, INFO on console, DEBUG in file
    logfile = setup_logging()
    log = get_logger("main")

    # Paths
    paths = ensure_paths()
    input_xlsx = Path(args.input).resolve()
    output_xlsx = Path(args.output).resolve()
    log.info("input=%s output=%s", input_xlsx, output_xlsx)

    # Load headers from the first sheet of input.xlsx (creates Input if missing)
    headers = load_headers(input_xlsx)

    # Prepare targets
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    if not targets:
        log.error("No targets provided after parsing --targets.")
        sys.exit(2)

    # Accumulate extracted records
    all_records: List[Dict[str, Any]] = []

    for project in targets:
        try:
            log.info("Processing %s", project)

            # Search (no SerpAPI dependency; uses guesses/curated/manual seeds)
            url_items = search_urls(project, target_count=int(os.getenv("MAX_URLS_PER_PROJECT", "10")))

            # Scrape → pages
            pages = scrape_urls(project, url_items)

            # Extract (LLM + seeds merge)
            rec = extract_record(
                project=project,
                headers=headers,
                pages=pages,
                provider=args.provider,
                model=args.model,
                max_output_tokens=args.max_output_tokens,
            )
            all_records.append(rec)

        except Exception as e:
            log.exception("Unhandled error while processing %s: %s", project, e)

    # Export: keep/append AI Data rows, rebuild Comparison fresh
    try:
        write_three_sheets(input_xlsx, headers, all_records, output_xlsx)
        log.info("Done → %s", output_xlsx)
    except Exception as e:
        log.exception("Failed to write Excel: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
