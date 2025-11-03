#!/usr/bin/env python3
"""
Accuracy checking utilities for validating AI extraction results.

Provides functions to compare AI-extracted data against manual/client data
and generate accuracy reports.
"""
import pandas as pd
import sys
from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional


def text_similarity(str1: str, str2: str) -> float:
    """
    Calculate similarity ratio between two strings (0.0 to 1.0).
    
    Args:
        str1: First string
        str2: Second string
    
    Returns:
        float: Similarity ratio from 0.0 (no match) to 1.0 (exact match)
    """
    if not str1 or not str2:
        return 0.0
    return SequenceMatcher(None, str1.lower().strip(), str2.lower().strip()).ratio()


def is_text_match(client_val: str, ai_val: str, field_name: str, threshold: float = 0.6) -> bool:
    """
    Check if two text values match.
    
    For short fields (URLs, dates, booleans), requires exact match.
    For long text fields, uses similarity threshold for semantic matching.
    
    Args:
        client_val: Manual/client-provided value
        ai_val: AI-extracted value
        field_name: Name of the field being compared
        threshold: Similarity threshold for long text fields (default 0.6)
    
    Returns:
        bool: True if values match, False otherwise
    """
    client_clean = str(client_val).strip() if pd.notna(client_val) else ""
    ai_clean = str(ai_val).strip() if pd.notna(ai_val) else ""
    
    # Empty handling
    if not client_clean or not ai_clean:
        return client_clean == ai_clean  # Both empty = match
    
    # Exact match (case-insensitive)
    if client_clean.lower() == ai_clean.lower():
        return True
    
    # For short fields (dates, URLs, booleans, status), require exact match
    # Also exclude source fields (Live Source, Archived Source) from matching
    if any(x in field_name.lower() for x in ['date', 'url', 'website', 'repository', 'source']):
        # But allow URL/website fields themselves (just not source fields)
        if 'live source' in field_name.lower() or 'archived source' in field_name.lower():
            return False  # Source fields should not be matched
        # For actual URL fields, check if it's a source field
        if 'source' in field_name.lower() and field_name.lower() != 'website':
            return False
    
    # For long text fields, use similarity threshold
    # Consider it a match if similarity is above threshold (default 60%)
    if len(client_clean) > 50 or len(ai_clean) > 50:  # Long text field
        similarity = text_similarity(client_clean, ai_clean)
        # Also check if key words/phrases overlap significantly
        client_words = set(word for word in client_clean.lower().split() if len(word) > 3)  # Skip short words
        ai_words = set(word for word in ai_clean.lower().split() if len(word) > 3)
        if len(client_words) > 0 and len(ai_words) > 0:
            word_overlap = len(client_words & ai_words) / len(client_words | ai_words) if len(client_words | ai_words) > 0 else 0
            # Also check if significant words (4+ chars) match
            significant_overlap = word_overlap * 1.3  # Boost significant word overlap
            # Use highest of similarity or word overlap
            final_score = max(similarity, significant_overlap)
            return final_score >= threshold
    
    # For short text fields, require exact match
    return False


def calculate_accuracy(
    output_xlsx: Path,
    projects: Optional[List[str]] = None,
    exclude_fields: Optional[List[str]] = None
) -> Tuple[Dict[str, Dict[str, float]], float]:
    """
    Calculate accuracy for each project and overall accuracy.
    
    Args:
        output_xlsx: Path to output Excel file with Comparison sheet
        projects: List of project names to analyze (None = all with data)
        exclude_fields: Fields to exclude from accuracy (None = use defaults)
    
    Returns:
        Tuple of (project_results, overall_accuracy) where:
        - project_results: Dict mapping project name to accuracy info
        - overall_accuracy: Overall accuracy percentage
    """
    # Default projects with manual data
    if projects is None:
        projects = ['MÁS', 'Trusted Biz', 'esatus', 'cheqd']
    
    # Default excluded fields
    if exclude_fields is None:
        exclude_fields = ['Live Source', 'Archived Source', 'Product Name', 'ID', 'Logo']
    
    # Load comparison sheet
    try:
        df_cmp = pd.read_excel(output_xlsx, sheet_name='Comparison', dtype=str)
    except FileNotFoundError:
        raise FileNotFoundError(f"{output_xlsx} not found. Run extraction first.")
    except Exception as e:
        raise RuntimeError(f"Error reading comparison sheet: {e}")
    
    project_results = {}
    total_fields = 0
    total_matches = 0
    
    for project in projects:
        proj_data = df_cmp[df_cmp['Project'] == project]
        if len(proj_data) == 0:
            project_results[project] = {'accuracy': 0.0, 'matches': 0, 'total': 0, 'found': False}
            continue
        
        # Filter out excluded fields for accuracy calculation
        exclude_pattern = '|'.join(exclude_fields)
        data_fields = proj_data[~proj_data['Field'].str.contains(exclude_pattern, case=False, na=False)]
        
        fields = len(data_fields)
        matches = len(data_fields[data_fields['Match?'] == '✅'])
        accuracy = (matches / fields * 100) if fields > 0 else 0
        
        project_results[project] = {
            'accuracy': accuracy,
            'matches': matches,
            'total': fields,
            'found': True
        }
        
        total_fields += fields
        total_matches += matches
    
    overall_accuracy = (total_matches / total_fields * 100) if total_fields > 0 else 0
    
    return project_results, overall_accuracy


def print_accuracy_report(output_xlsx: Path, projects: Optional[List[str]] = None) -> None:
    """
    Print a formatted accuracy report.
    
    Args:
        output_xlsx: Path to output Excel file with Comparison sheet
        projects: List of project names to analyze (None = all with data)
    """
    project_results, overall_accuracy = calculate_accuracy(output_xlsx, projects)
    
    # Load comparison sheet for mismatch details
    try:
        df_cmp = pd.read_excel(output_xlsx, sheet_name='Comparison', dtype=str)
    except Exception:
        df_cmp = None
    
    print("=" * 70)
    print("Accuracy Analysis for Projects with Manual Data")
    print("=" * 70)
    print("Note: Only Data Columns included (Product Name, ID, and source fields excluded)")
    print("      Text fields use semantic similarity matching (threshold: 60%)")
    print()
    
    for project, results in project_results.items():
        if not results.get('found'):
            print(f"{project}: ⚠️  Not found in comparison sheet")
            continue
        
        accuracy = results['accuracy']
        matches = results['matches']
        total = results['total']
        
        status = "✅" if accuracy >= 60 else "⚠️" if accuracy >= 30 else "❌"
        print(f"{status} {project}:")
        print(f"   Accuracy: {accuracy:.1f}% ({matches}/{total} fields match)")
        
        # Show top mismatches if we have the comparison data
        if df_cmp is not None:
            proj_data = df_cmp[df_cmp['Project'] == project]
            exclude_fields = ['Live Source', 'Archived Source', 'Product Name', 'ID', 'Logo']
            exclude_pattern = '|'.join(exclude_fields)
            data_fields = proj_data[~proj_data['Field'].str.contains(exclude_pattern, case=False, na=False)]
            mismatches = data_fields[data_fields['Match?'] != '✅']
            
            if len(mismatches) > 0:
                print(f"   Top 3 mismatches:")
                for _, row in mismatches.head(3).iterrows():
                    client_val = str(row['Client Value']) if pd.notna(row['Client Value']) else 'N/A'
                    ai_val = str(row['AI Value']) if pd.notna(row['AI Value']) else 'N/A'
                    client_val = client_val[:40] + '...' if len(client_val) > 40 else client_val
                    ai_val = ai_val[:40] + '...' if len(ai_val) > 40 else ai_val
                    print(f"     • {row['Field']}:")
                    print(f"       Client: {client_val}")
                    print(f"       AI:     {ai_val}")
        print()
    
    print("=" * 70)
    
    # Calculate total matches/total fields
    total_matches = sum(r['matches'] for r in project_results.values())
    total_fields = sum(r['total'] for r in project_results.values())
    
    print(f"Overall Accuracy: {overall_accuracy:.1f}% ({total_matches}/{total_fields} fields)")
    print("=" * 70)
    
    if overall_accuracy >= 60:
        print("✅ Excellent! Ready for Phase 2 (empty projects)")
    elif overall_accuracy >= 40:
        print("⚠️  Good progress, but may need further tuning")
    else:
        print("❌ Needs improvement. Check SerpAPI key and sources.")


def main():
    """Main entry point for running accuracy check from command line."""
    output_xlsx = Path('data/output.xlsx')
    
    if not output_xlsx.exists():
        print("Error: data/output.xlsx not found. Run extraction first.")
        sys.exit(1)
    
    print_accuracy_report(output_xlsx)


if __name__ == '__main__':
    main()

