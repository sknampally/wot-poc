"""
Main entry point for the Web of Trust POC (Proof of Concept).

This script orchestrates the complete data extraction pipeline:
1. Search: Uses SerpAPI to find relevant URLs for each project
2. Scrape: Fetches and extracts text content from those URLs
3. Extract: Uses LLM (OpenAI, Ollama, or Perplexity) to extract structured data
4. Export: Writes results to Excel with comparison against manual data

Usage:
    python src/main.py --targets "project1,project2" --provider openai --model gpt-4o-mini

Environment variables (via .env file):
    - SERPAPI_API_KEY: Required for web search
    - OPENAI_API_KEY: Required for LLM extraction (if using OpenAI)
    - PERPLEXITY_API_KEY: Required for Perplexity fallback
    - LLM_PROVIDER: 'openai' or 'ollama' (default: 'openai')
    - LLM_MODEL: Model name (default: 'gpt-4o-mini')
    - LLM_MAX_TOKENS: Maximum output tokens (default: 4000)
"""
import argparse
import json
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

from app.utils.logger import setup_logging
from app.utils.accuracy import print_accuracy_report
from app.utils.codebook_import import import_excel_to_codebook
from app.workers.searcher import search_urls
from app.workers.scraper import scrape_urls
from app.workers.extractor import extract_record
from app.workers.llm_client import chat_json
from app.core.export_excel import export_to_excel
from app.core.schema import load_headers

log = logging.getLogger(__name__)

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
        required=False,  # Not required if --check-accuracy is used
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
    p.add_argument(
        "--check-accuracy",
        action="store_true",
        help="Run accuracy check on existing output.xlsx and exit"
    )
    p.add_argument(
        "--import-codebook",
        type=str,
        metavar="EXCEL_PATH",
        help="Import codebook from Excel file and convert to JSON, then exit"
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
    
    # Handle --check-accuracy flag (quick accuracy check without running extraction)
    if args.check_accuracy:
        output_xlsx = DATA_DIR / "output.xlsx"
        print_accuracy_report(output_xlsx)
        return
    
    # Handle --import-codebook flag (import Excel to JSON)
    if args.import_codebook:
        try:
            excel_path = args.import_codebook
            import_excel_to_codebook(excel_path)
            print("\n✅ Codebook import complete!")
            log.info("[main] Codebook imported from %s", excel_path)
            return
        except Exception as e:
            print(f"\n❌ Error importing codebook: {e}")
            log.error("[main] Codebook import failed: %s", e, exc_info=True)
            sys.exit(1)
    
    # Validate targets are provided for extraction
    if not args.targets:
        print("Error: --targets is required for extraction. Use --check-accuracy or --import-codebook for utility functions.")
        sys.exit(1)

    # Define input/output Excel files
    input_xlsx = DATA_DIR / "input.xlsx"  # Source data with project names
    output_xlsx = DATA_DIR / "output.xlsx"  # Results with AI extraction and comparison
    print(f"input={input_xlsx} output={output_xlsx}")

    # Load codebook to get field definitions and determine what to extract
    from app.config.codebook import load_codebook, load_prompts
    codebook = load_codebook()
    prompts_config = load_prompts()  # Load all prompts from prompts.json
    
    # Build all headers list from codebook (data + source + archived columns in order)
    # For POC, we include all columns from codebook regardless of source_needed/archive_needed
    # This ensures output.xlsx matches the expected structure from definitions file
    all_headers_list = []
    for field in codebook.fields:
        data_col = field.get("data_column", "")
        source_col = field.get("source_column", "")
        archived_col = field.get("archived_column", "")
        
        # Add data column
        if data_col:
            all_headers_list.append(data_col)
        # Add source column if present
        if source_col:
            all_headers_list.append(source_col)
        # Add archived column if present (even if not needed, to match structure)
        if archived_col:
            all_headers_list.append(archived_col)
    
    # Get data columns that need extraction (extraction_needed == "Y")
    # CRITICAL: Also include Product Name, ID, Logo for proper header mapping
    # These are needed even if extraction_needed=N for data row structure
    headers_for_extraction = codebook.get_data_columns_needed()
    headers_for_comparison = headers_for_extraction + ["Product Name", "ID", "Logo"]
    headers = headers_for_extraction  # Use only extraction_needed=Y for LLM
    
    print(f"Loaded {len(codebook.fields)} fields from codebook")
    print(f"Extracting data for {len(headers)} fields where extraction_needed=Y")

    # Parse project names from --targets argument
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    if not targets:
        raise SystemExit("No targets parsed from --targets")

    # Load input data to get known websites and GitHub repos for better search targeting
    # Known websites and GitHub repos help the system prioritize official sources
    import pandas as pd
    df_input = pd.read_excel(input_xlsx, sheet_name=0, dtype=str).fillna("")
    
    # Find the column names for project name, website, and GitHub repo
    name_col = None
    website_col = None
    github_col = None
    for col in df_input.columns:
        if col.lower() in ["product name", "project name", "name"]:
            name_col = col
        if col.lower() == "website":
            website_col = col
        if "code repository" in col.lower() and "public" in col.lower():
            github_col = col
    
    # Build mapping: project_name -> website URL
    # This helps the search phase target the correct entity
    project_websites = {}
    project_github = {}
    if name_col:
        for _, row in df_input.iterrows():
            proj_name = str(row.get(name_col, "")).strip()
            if not proj_name:
                continue
                
            # Extract website if available
            if website_col:
                website = str(row.get(website_col, "")).strip()
                if website and website.lower() not in ("nan", "none", ""):
                    project_websites[proj_name] = website
            
            # Extract GitHub repository if available
            if github_col:
                github = str(row.get(github_col, "")).strip()
                if github and github.lower() not in ("nan", "none", "") and "github.com" in github.lower():
                    project_github[proj_name] = github
    
    # Process each project through the pipeline
    all_rows = []
    for project in targets:
        print(f"Processing {project}")
        
        # Get known website and GitHub repo if available (helps improve search accuracy)
        known_website = project_websites.get(project, "")
        known_github = project_github.get(project, "")
        
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
        # Pass headers_for_comparison (includes Product Name) to ensure proper header mapping
        rec = extract_record(
            project=project,
            headers=headers_for_comparison,
            pages=pages,
            provider=args.provider,
            model=args.model,
            max_output_tokens=args.max_output_tokens,
            known_website=known_website,  # Helps identify official website
        )
        
        # Add Product Name to the record (since extraction_needed=N in codebook)
        rec["Product Name"] = project
        
        # Step 4: Perplexity fallback for empty OR "Failed to disclose" key fields
        # Use Perplexity's web search for fields that the primary LLM couldn't extract
        # At $0.0005 per query, it's cost-effective to query ALL fields with Perplexity config
        missing_key_fields = []
        
        # Load fields config to check which fields have Perplexity config
        fields_config = prompts_config.get("fields", {})
        
        # Check for empty or "Failed to disclose" values in ALL fields that have Perplexity config
        def _should_try_perplexity(field_name: str) -> bool:
            # Only try Perplexity if field has a query_template configured
            if field_name not in fields_config or "perplexity_query_template" not in fields_config[field_name]:
                return False
            # Check if use_perplexity is enabled (Y) for this field
            use_perplexity = fields_config[field_name].get("use_perplexity", "N")
            if use_perplexity != "Y":
                return False
            val = rec.get(field_name, "").strip()
            # Try Perplexity if empty OR if LLM returned "Failed to disclose"
            return val == "" or val.lower() == "failed to disclose"
        
        # Check ALL headers for missing fields with Perplexity config
        for field in headers_for_extraction:
            if _should_try_perplexity(field):
                missing_key_fields.append(field)
        
        if missing_key_fields and os.getenv("PERPLEXITY_API_KEY"):
            log.info("[perplexity] Fallback for %s: %d missing fields", project, len(missing_key_fields))
            print(f"  → Trying Perplexity for {len(missing_key_fields)} missing fields...")
            
            try:
                for field in missing_key_fields:
                    # Query Perplexity to search the web for this specific field
                    # Load prompt from prompts.json if available, otherwise use default
                    # fields_config already loaded above
                    field_config = fields_config.get(field)
                    
                    if field_config and "perplexity_query_template" in field_config:
                        # Use config from prompts.json v2.0 format
                        query_template = field_config.get("perplexity_query_template", f"{field.lower()} for {project}")
                        strict_prompt = field_config.get("perplexity_system_prompt", "Extract and return ONLY the exact value requested. No explanations, no formatting, no additional text. Just the value itself.")
                        max_tokens = field_config.get("perplexity_max_tokens", 200)
                        
                        # Format query with project name
                        query = query_template.format(project=project)
                        if known_website:
                            # Add website hint if available
                            query += f" from {known_website}"
                    else:
                        # Fallback to default
                        query = f"{field.lower()} for {project}"
                        if known_website:
                            query += f" from {known_website}"
                        max_tokens = 200
                        strict_prompt = "Extract and return ONLY the exact value requested. No explanations, no formatting, no additional text. Just the value itself."
                    
                    perplexity_response = chat_json(
                        system=strict_prompt,
                        user=query,
                        provider="perplexity",
                        model="sonar",
                        max_tokens=max_tokens,
                    )
                    
                    if perplexity_response.strip():
                        # Post-process Perplexity response to extract clean value
                        cleaned = perplexity_response.strip()
                        
                        # For Logo, extract first image URL or brand assets page
                        if field == "Logo":
                            import re
                            # First try to find direct image URLs (png, svg, jpg, webp, etc.)
                            img_urls = re.findall(r'https?://[^\s\[\]]+\.(?:png|svg|jpg|jpeg|webp|gif|ico)', cleaned, re.IGNORECASE)
                            if img_urls:
                                cleaned = img_urls[0]
                            else:
                                # Otherwise look for brand assets pages (brandfetch, etc.)
                                brand_urls = re.findall(r'https?://[^\s\[\]]*(?:brandfetch|brandassets|logo|brand)[^\s\[\]]*', cleaned, re.IGNORECASE)
                                if brand_urls:
                                    cleaned = brand_urls[0]
                            # Remove markdown formatting and trailing punctuation
                            cleaned = cleaned.replace('**', '').replace('*', '').rstrip('.,;')
                        
                        # For Public Code Repository, extract first GitHub/GitLab URL
                        elif field == "Public Code Repository":
                            import re
                            urls = re.findall(r'https?://(?:github|gitlab)\.com/[^\s\[\]]+', cleaned, re.IGNORECASE)
                            if urls:
                                # Prefer organization repos over specific repos if both exist
                                org_url = [u for u in urls if re.match(r'https://github\.com/[^/]+/?$', u)]
                                if org_url:
                                    cleaned = org_url[0].rstrip('/')
                                else:
                                    cleaned = urls[0]
                            # Remove markdown formatting
                            cleaned = cleaned.replace('**', '').replace('*', '')
                        
                        # For Project Launch Date, extract first 4-digit year
                        elif field == "Project Launch Date":
                            import re
                            years = re.findall(r'\b(19|20\d{2})\b', cleaned)
                            if years:
                                cleaned = years[0]
                        
                        # For Project Announcement Date, extract first 4-digit year
                        elif field == "Project Announcement Date":
                            import re
                            years = re.findall(r'\b(19|20\d{2})\b', cleaned)
                            if years:
                                cleaned = years[0]
                        
                        # For Status field, extract status name from response
                        elif field == "Status":
                            # Look for status keywords in the response
                            statuses = ["Announced", "Pilot", "Launched", "Discontinued"]
                            found_status = None
                            for status in statuses:
                                if status.lower() in cleaned.lower():
                                    found_status = status
                                    break
                            if found_status:
                                cleaned = found_status
                        
                        # For ZKP field, normalize to True/False/Failed to disclose
                        elif field == "Uses/endorses ZKP":
                            # Map Perplexity response to our format
                            cleaned_lower = cleaned.lower()
                            if "yes" in cleaned_lower or "true" in cleaned_lower:
                                cleaned = "True"
                            elif "no" in cleaned_lower or "false" in cleaned_lower:
                                cleaned = "False"
                            elif "failed to disclose" in cleaned_lower:
                                cleaned = "Failed to disclose"
                            else:
                                # Default if unclear
                                cleaned = "Failed to disclose"
                        
                        # For DLT Data Availability, extract only the exact enum value
                        elif field == "DLT Data Availability":
                            # Map to exact enum values
                            dlt_values = [
                                "Complete DLT Data",
                                "Specifies DLT Technology but no DLT Instance",
                                "Uses DLT no specifications",
                                "No DLT data at all"
                            ]
                            cleaned_lower = cleaned.lower()
                            for val in dlt_values:
                                if val.lower() in cleaned_lower:
                                    cleaned = val
                                    break
                        
                        # For Has Exportable Credentials, normalize to True/False/Failed to disclose
                        elif field == "Has Exportable Credentials":
                            cleaned_lower = cleaned.lower()
                            if "yes" in cleaned_lower or "true" in cleaned_lower:
                                cleaned = "True"
                            elif "no" in cleaned_lower or "false" in cleaned_lower:
                                cleaned = "False"
                            elif "failed to disclose" in cleaned_lower:
                                cleaned = "Failed to disclose"
                            else:
                                # Default if unclear
                                cleaned = "Failed to disclose"
                        
                        # For Does it use SSI Technology?, normalize to True/False only
                        elif field == "Does it use SSI Technology?":
                            cleaned_lower = cleaned.lower()
                            if "yes" in cleaned_lower or "true" in cleaned_lower:
                                cleaned = "True"
                            elif "no" in cleaned_lower or "false" in cleaned_lower:
                                cleaned = "False"
                            else:
                                # Use empty string for boolean fields not in allowed list
                                cleaned = ""
                        
                        # For Politically Involved?, normalize to True/False only
                        elif field == "Politically Involved?":
                            cleaned_lower = cleaned.lower()
                            if "yes" in cleaned_lower or "true" in cleaned_lower:
                                cleaned = "True"
                            elif "no" in cleaned_lower or "false" in cleaned_lower:
                                cleaned = "False"
                            else:
                                # Use empty string for boolean fields not in allowed list
                                cleaned = ""
                        
                        # For Credential And Key Storage, normalize to True/False/Failed to disclose
                        elif field == "Credential And Key Storage":
                            cleaned_lower = cleaned.lower()
                            if "yes" in cleaned_lower or "true" in cleaned_lower:
                                cleaned = "True"
                            elif "no" in cleaned_lower or "false" in cleaned_lower:
                                cleaned = "False"
                            elif "failed to disclose" in cleaned_lower:
                                cleaned = "Failed to disclose"
                            else:
                                # Default if unclear
                                cleaned = "Failed to disclose"
                        
                        # For Targets fields (Holders, Issuers, Verifiers), normalize to True/False/Failed to disclose
                        elif field in ["Targets Holders", "Targets Issuers", "Targets Verifiers"]:
                            cleaned_lower = cleaned.lower().strip()
                            # Remove common punctuation/formatting that Perplexity might add
                            cleaned_lower = cleaned_lower.rstrip('.,;!?').strip()
                            
                            # Check for True/False more aggressively - look for exact word matches or as first word
                            if (cleaned_lower == "true" or cleaned_lower.startswith("true ") or 
                                "true" in cleaned_lower.split()[:2] or "yes" in cleaned_lower.split()[:2]):
                                cleaned = "True"
                            elif (cleaned_lower == "false" or cleaned_lower.startswith("false ") or 
                                  "false" in cleaned_lower.split()[:2] or "no" in cleaned_lower.split()[:2]):
                                cleaned = "False"
                            elif "failed to disclose" in cleaned_lower:
                                cleaned = "Failed to disclose"
                            else:
                                # Default if unclear
                                cleaned = "Failed to disclose"
                        
                        # For long responses (Mission, Tech Stack), summarize using OpenAI if verbose
                        elif len(cleaned) > 200 and field in ["Mission Statement", "Tech Stack Descriptions"]:
                            try:
                                log.info("[perplexity] %s: Summarizing verbose %s response", project, field)
                                summary = chat_json(
                                    system="You are a precise summarizer. Extract ONLY the core information. No explanations, no formatting.",
                                    user=f"Summarize this into a concise {field.lower()}: {cleaned[:500]}",
                                    provider="openai",
                                    model=args.model,
                                    max_tokens=200,
                                )
                                if summary.strip():
                                    cleaned = summary.strip()
                                    log.info("[perplexity] %s: Summarized %s", project, field)
                            except Exception as e:
                                log.warning("[perplexity] %s: Summary failed for %s: %s", project, field, e)
                        
                        # For other fields, take first line if response is long and remove markdown
                        else:
                            if len(cleaned) > 200:
                                cleaned = cleaned.split('\n')[0].strip()
                            # Remove markdown formatting
                            cleaned = cleaned.replace('**', '').replace('*', '').replace('_', '').strip()
                        
                        rec[field] = cleaned
                        log.info("[perplexity] %s: Extracted %s", project, field)
                        print(f"    ✓ Found {field}")
                        
                        # Bonus: If response contains Person of Interest details, extract them
                        # This helps capture founder/leader info from any response
                        if field in ["Status", "Project Announcement Date", "Mission Statement", "Tech Stack Descriptions"]:
                            try:
                                import re
                                # Look for common founder/leader patterns in the response
                                founder_patterns = [
                                    r'founded by ([A-Z][a-z]+ [A-Z][a-z]+)',
                                    r'founders?: ([A-Z][a-z]+ [A-Z][a-z]+(?: and [A-Z][a-z]+ [A-Z][a-z]+)?)',
                                    r'co-founded by ([A-Z][a-z]+ [A-Z][a-z]+)',
                                    r'CEO ([A-Z][a-z]+ [A-Z][a-z]+)',
                                    r'led by ([A-Z][a-z]+ [A-Z][a-z]+)',
                                ]
                                names_found = []
                                for pattern in founder_patterns:
                                    matches = re.findall(pattern, cleaned)
                                    names_found.extend(matches)
                                
                                if names_found and not rec.get("Person of Interest", "").strip():
                                    # Store first few names found
                                    unique_names = list(dict.fromkeys(names_found))  # Remove duplicates while preserving order
                                    rec["Person of Interest"] = ", ".join(unique_names[:3])  # Max 3 names
                                    log.info("[perplexity] %s: Bonus - extracted Person of Interest from %s response", project, field)
                            except Exception as e:
                                log.debug("[perplexity] %s: Error extracting Person of Interest from %s: %s", project, field, e)
                    else:
                        log.warning("[perplexity] %s: No response for %s", project, field)
            except Exception as e:
                log.error("[perplexity] %s: Error during fallback: %s", project, e, exc_info=True)
                print(f"    ⚠ Perplexity fallback failed: {e}")
        
        all_rows.append(rec)

    # Step 4: Export all results to Excel
    # Creates Input, AI, and Comparison sheets
    export_to_excel(
        input_xlsx=input_xlsx,
        headers=headers_for_comparison,  # Data columns for comparison (includes Product Name, ID, Logo)
        recs=all_rows,
        output_xlsx=output_xlsx,
        all_headers=all_headers_list,  # Include source columns in AI sheet for review
    )
    print(f"Done → {output_xlsx}")
    
    # Step 5: Optional accuracy check (controlled by .env parameter)
    auto_check_accuracy = os.getenv("AUTO_CHECK_ACCURACY", "false").lower() == "true"
    if auto_check_accuracy:
        log.info("[main] Running automatic accuracy check...")
        print("\n" + "=" * 70)
        print("AUTOMATIC ACCURACY CHECK")
        print("=" * 70)
        print_accuracy_report(output_xlsx)

if __name__ == "__main__":
    main()
