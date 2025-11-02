"""
Main entry point for the Web of Trust POC (Proof of Concept).

This script orchestrates the complete data extraction pipeline:
1. Search: Uses SerpAPI to find relevant URLs for each project
2. Scrape: Fetches and extracts text content from those URLs
3. Extract: Uses LLM (OpenAI or Ollama) to extract structured data
4. Export: Writes results to Excel with comparison against manual data

Usage:
    python src/main.py --targets "project1,project2" --provider openai --model gpt-4o-mini

Environment variables (via .env file):
    - SERPAPI_API_KEY: Required for web search
    - OPENAI_API_KEY: Required for LLM extraction (if using OpenAI)
    - LLM_PROVIDER: 'openai' or 'ollama' (default: 'openai')
    - LLM_MODEL: Model name (default: 'gpt-4o-mini')
    - LLM_MAX_TOKENS: Maximum output tokens (default: 4000)
"""
import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv

from app.utils.logger import setup_logging
from app.workers.searcher import search_urls
from app.workers.scraper import scrape_urls
from app.workers.extractor import extract_record
from app.core.export_excel import export_to_excel
from app.core.schema import load_headers

# Directory paths relative to project root
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CACHE_DIR = DATA_DIR / "cache"  # Cache directory for scraped content
LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"

def parse_args():
    """
    Parse command-line arguments.
    
    Returns:
        argparse.Namespace: Parsed arguments with:
            - targets: Comma-separated project names (or "all")
            - provider: LLM provider ('openai' or 'ollama')
            - model: Model name to use
            - max_output_tokens: Maximum tokens for LLM output
    """
    p = argparse.ArgumentParser(
        description="Extract structured data for digital identity projects from web sources"
    )
    p.add_argument(
        "--targets", 
        type=str, 
        required=True, 
        help='Comma or space separated project names, e.g. "cheqd, Trusted Biz" or "all"'
    )
    p.add_argument(
        "--provider", 
        type=str, 
        default=os.getenv("LLM_PROVIDER", "openai"),
        help="LLM provider: 'openai' or 'ollama'"
    )
    p.add_argument(
        "--model", 
        type=str, 
        default=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        help="Model name (e.g., 'gpt-4o-mini', 'llama3.1')"
    )
    p.add_argument(
        "--max-output-tokens", 
        type=int, 
        default=int(os.getenv("LLM_MAX_TOKENS", "4000")),
        help="Maximum tokens for LLM response"
    )
    return p.parse_args()

def main():
    """
    Main execution function that orchestrates the data extraction pipeline.
    
    Pipeline steps:
    1. Load environment variables and initialize logging
    2. Load headers/columns from input.xlsx
    3. For each project:
       a. Search for relevant URLs using SerpAPI
       b. Scrape content from those URLs
       c. Extract structured data using LLM
    4. Export all results to output.xlsx with comparison sheet
    """
    # Load environment variables from .env file
    load_dotenv()
    
    # Initialize logging (logs to logs/wot.log and optionally console)
    setup_logging()
    print(f"Logging initialized at {LOGS_DIR / 'wot.log'}")

    # Parse command-line arguments
    args = parse_args()

    # Define input/output Excel files
    input_xlsx = DATA_DIR / "input.xlsx"  # Source data with project names
    output_xlsx = DATA_DIR / "output.xlsx"  # Results with AI extraction and comparison
    print(f"input={input_xlsx} output={output_xlsx}")

    # Load column headers from input.xlsx (these define what data to extract)
    all_headers = load_headers(input_xlsx)
    
    # Separate data columns from source columns and internal/excluded identifiers
    # Data Definition columns: actual data fields we extract (e.g., "Mission Statement", "Status")
    # Source columns: URLs where we found the data (e.g., "Live Source Mission Statement", "Archived Source Mission Statement")
    # Internal/excluded columns: ID (unique identifier), Logo (URL to image - not text to extract)
    # Note: Logo is typically a URL to an image file, not content that can be extracted from web pages
    data_columns = [h for h in all_headers if "Live Source" not in h and "Archived Source" not in h 
                    and h.strip() != "ID" and h.strip() != "Logo"]
    source_columns = [h for h in all_headers if "Live Source" in h or "Archived Source" in h]
    internal_columns = [h for h in all_headers if h.strip() in ["ID", "Logo"]]
    
    # For extraction, we only use data columns (sources are populated from evidence URLs, ID and Logo are excluded)
    headers = data_columns
    
    # Store all headers (including sources) for output Excel sheet structure
    all_headers_list = all_headers
    
    print(f"Loaded {len(data_columns)} data columns, {len(source_columns)} source columns, and {len(internal_columns)} internal/excluded columns from input.xlsx")
    print(f"Extracting data for {len(headers)} data definition columns only (ID and Logo excluded - internal/excluded fields).")

    # Parse project names from --targets argument
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    if not targets:
        raise SystemExit("No targets parsed from --targets")

    # Load input data to get known websites for better search targeting
    # Known websites help the system prioritize official sources
    import pandas as pd
    df_input = pd.read_excel(input_xlsx, sheet_name=0, dtype=str).fillna("")
    
    # Find the column names for project name and website
    name_col = None
    website_col = None
    for col in df_input.columns:
        if col.lower() in ["product name", "project name", "name"]:
            name_col = col
        if col.lower() == "website":
            website_col = col
            break
    
    # Build mapping: project_name -> website URL
    # This helps the search phase target the correct entity
    project_websites = {}
    if name_col and website_col:
        for _, row in df_input.iterrows():
            proj_name = str(row.get(name_col, "")).strip()
            website = str(row.get(website_col, "")).strip()
            # Only include valid website entries (not empty/None/Nan)
            if proj_name and website and website.lower() not in ("nan", "none", ""):
                project_websites[proj_name] = website
    
    # Process each project through the pipeline
    all_rows = []
    for project in targets:
        print(f"Processing {project}")
        
        # Get known website if available (helps improve search accuracy)
        known_website = project_websites.get(project, "")
        
        # Step 1: Search for relevant URLs using SerpAPI
        # This finds official websites, documentation, blog posts, etc.
        url_items = search_urls(
            project, 
            cache_dir=CACHE_DIR / project, 
            known_website=known_website
        )
        
        # Step 2: Scrape content from the collected URLs
        # Extracts clean text from HTML pages
        pages = scrape_urls(
            project, 
            url_items=url_items, 
            cache_dir=CACHE_DIR / project
        )
        
        # Step 3: Extract structured data using LLM
        # Uses codebook definitions to guide extraction
        rec = extract_record(
            project=project,
            headers=headers,
            pages=pages,
            provider=args.provider,
            model=args.model,
            max_output_tokens=args.max_output_tokens,
            known_website=known_website,  # Helps identify official website
        )
        all_rows.append(rec)

    # Step 4: Export all results to Excel
    # Creates Input, AI, and Comparison sheets
    export_to_excel(
        input_xlsx=input_xlsx,
        headers=headers,  # Data columns for extraction and comparison
        recs=all_rows,
        output_xlsx=output_xlsx,
        all_headers=all_headers_list,  # Include source columns in AI sheet for review
    )
    print(f"Done → {output_xlsx}")

if __name__ == "__main__":
    main()
