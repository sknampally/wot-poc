#!/usr/bin/env python3
"""
Import data definitions from Excel and convert to codebook JSON.

This utility allows stakeholders to update field definitions in Excel format,
which is then automatically converted to the structured codebook.json format
used by the extraction pipeline.

Expected Excel columns:
- Data Column
- Response Type  
- Extraction Needed (Y/N)
- Data Defination / LLM Prompt Rules
- Source of Data
- Response Type.1
- Source Needed (Y/N)
- Archived Location of Source Data
- Response Type.2
- Archive Needed (Y/N)
"""
import pandas as pd
import json
from pathlib import Path
import logging

log = logging.getLogger(__name__)


def import_excel_to_codebook(excel_path: str, output_json_path: str = None) -> dict:
    """
    Read Excel file and convert to codebook structure in new v2.0 format.
    
    Args:
        excel_path: Path to Excel file with field definitions
        output_json_path: Optional path for output JSON (defaults to data/codebook.json)
    
    Returns:
        dict: Codebook structure suitable for JSON export
    
    Raises:
        FileNotFoundError: If Excel file doesn't exist
    """
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")
    
    df = pd.read_excel(excel_path, sheet_name=0)  # Read first sheet
    log.info("Loaded Excel with %d rows, %d columns", len(df), len(df.columns))
    print(f"Loaded Excel with {len(df)} rows, {len(df.columns)} columns")
    print(f"Columns: {list(df.columns)}")
    
    # Initialize codebook structure with new v2.0 format
    codebook = {
        "version": "2.0",
        "fields": [],  # v2.0 format: array of field objects
        "status_enums": ["Announced", "Pilot", "Launched", "Discontinued"],
        "ternary_enums": ["True", "False", "Failed to disclose"],
        "year_fields": [],
        "normalize": {
            "status": {},
            "ternary": {}
        },
        "field_synonyms": {}
    }
    
    # Auto-detect column names (flexible matching for the expected Excel structure)
    data_col = None
    response_type_col = None
    extraction_needed_col = None
    data_def_col = None
    source_col = None
    source_response_col = None
    source_needed_col = None
    archived_col = None
    archived_response_col = None
    archive_needed_col = None
    
    for col in df.columns:
        col_lower = str(col).lower()
        if data_col is None and any(x in col_lower for x in ["data column", "field", "column", "name"]):
            data_col = col
        if response_type_col is None and any(x in col_lower for x in ["response type"]) and not col.endswith('.1') and not col.endswith('.2'):
            response_type_col = col
        if extraction_needed_col is None and any(x in col_lower for x in ["extraction needed", "extraction_needed"]):
            extraction_needed_col = col
        if data_def_col is None and any(x in col_lower for x in ["data definition", "data defination", "prompt rules", "llm prompt", "description", "definition"]):
            data_def_col = col
        if source_col is None and any(x in col_lower for x in ["source of data", "source"]) and "needed" not in col_lower and "archived" not in col_lower:
            source_col = col
        if source_response_col is None and any(x in col_lower for x in ["response type.1", "source response"]):
            source_response_col = col
        if source_needed_col is None and any(x in col_lower for x in ["source needed", "source_needed"]):
            source_needed_col = col
        if archived_col is None and any(x in col_lower for x in ["archived location", "archived source"]):
            archived_col = col
        if archived_response_col is None and any(x in col_lower for x in ["response type.2", "archived response"]):
            archived_response_col = col
        if archive_needed_col is None and any(x in col_lower for x in ["archive needed", "archive_needed"]):
            archive_needed_col = col
    
    print(f"\nDetected columns:")
    print(f"  Data Column: {data_col}")
    print(f"  Response Type: {response_type_col}")
    print(f"  Extraction Needed: {extraction_needed_col}")
    print(f"  Data Definition: {data_def_col}")
    print(f"  Source of Data: {source_col}")
    print(f"  Source Response Type: {source_response_col}")
    print(f"  Source Needed: {source_needed_col}")
    print(f"  Archived Location: {archived_col}")
    print(f"  Archived Response Type: {archived_response_col}")
    print(f"  Archive Needed: {archive_needed_col}")
    
    # Process each row to build fields array
    for idx, row in df.iterrows():
        data_column = str(row.get(data_col, "")).strip() if data_col and pd.notna(row.get(data_col)) else ""
        if not data_column or data_column.lower() in ["nan", "none", ""]:
            continue
        
        # Build field object in v2.0 format
        field_obj = {
            "data_column": data_column,
            "response_type": str(row.get(response_type_col, "")).strip() if response_type_col and pd.notna(row.get(response_type_col)) else "[text]",
            "extraction_needed": str(row.get(extraction_needed_col, "N")).strip().upper() if extraction_needed_col and pd.notna(row.get(extraction_needed_col)) else "N",
            "data_definition": str(row.get(data_def_col, "")).strip() if data_def_col and pd.notna(row.get(data_def_col)) else "",
            "source_column": str(row.get(source_col, "")).strip() if source_col and pd.notna(row.get(source_col)) else "",
            "source_response_type": str(row.get(source_response_col, "")).strip() if source_response_col and pd.notna(row.get(source_response_col)) else "[URL]",
            "source_needed": str(row.get(source_needed_col, "N")).strip().upper() if source_needed_col and pd.notna(row.get(source_needed_col)) else "N",
            "archived_column": str(row.get(archived_col, "")).strip() if archived_col and pd.notna(row.get(archived_col)) else "",
            "archived_response_type": str(row.get(archived_response_col, "")).strip() if archived_response_col and pd.notna(row.get(archived_response_col)) else "[URL]",
            "archive_needed": str(row.get(archive_needed_col, "N")).strip().upper() if archive_needed_col and pd.notna(row.get(archive_needed_col)) else "N",
        }
        
        codebook["fields"].append(field_obj)
        
        # Auto-detect year fields based on field name
        if any(x in data_column.lower() for x in ["announcement", "launch", "date"]):
            if data_column not in codebook["year_fields"]:
                codebook["year_fields"].append(data_column)
    
    # Save to JSON
    if output_json_path is None:
        # Default to project data directory
        project_root = Path(__file__).resolve().parents[3]
        output_json_path = project_root / "data" / "codebook.json"
    else:
        output_json_path = Path(output_json_path)
    
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(codebook, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Codebook saved to: {output_json_path}")
    print(f"   Total fields defined: {len(codebook['fields'])}")
    print(f"   Format: v2.0 (fields array)")
    log.info("Codebook import complete: %d fields defined", len(codebook['fields']))
    
    return codebook


def main():
    """Main entry point for running import from command line."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m app.utils.codebook_import <excel_file_path> [output_json_path]")
        print("\nExample:")
        print("  python -m app.utils.codebook_import data/wot_data_definations.xlsx")
        print("\nOr via main.py:")
        print("  python src/main.py --import-codebook data/wot_data_definations.xlsx")
        sys.exit(1)
    
    excel_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        codebook = import_excel_to_codebook(excel_path, output_path)
        print("\n✅ Import complete!")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}")
        log.error("Codebook import failed: %s", e, exc_info=True)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
