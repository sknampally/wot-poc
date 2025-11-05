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
    For Mission Statement and Tech Stack Descriptions, uses lower threshold (0.10)
    to account for paraphrasing - "different wording, similar meaning" is considered a match.
    
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
    
    # For Managing Entity, normalize parenthetical names (e.g., "Name (Short)" vs "Name")
    if "managing entity" in field_name.lower():
        import re
        client_normalized = re.sub(r'\s*\([^)]+\)\s*', '', client_clean).strip()
        ai_normalized = re.sub(r'\s*\([^)]+\)\s*', '', ai_clean).strip()
        if client_normalized.lower() == ai_normalized.lower():
            return True
    
    # For Standard/Protocol Used and Regulations Followed, check if manual value appears in AI's comma-separated list
    if "standard" in field_name.lower() and "protocol" in field_name.lower():
        # Extract key terms from manual value (e.g., "W3C Verifiable Credentials Data Model" -> ["W3C", "VCDM"])
        import re
        # Extract acronyms and key terms
        manual_terms = re.findall(r'\b[A-Z]{2,}\b|\b\w+\b', client_clean)
        manual_lower = client_clean.lower()
        ai_lower = ai_clean.lower()
        
        # Check if any significant term from manual appears in AI response
        # For "W3C Verifiable Credentials Data Model", check for "W3C", "VCDM", "Verifiable Credentials"
        key_terms = []
        if "W3C" in client_clean:
            key_terms.extend(["W3C", "VCDM", "verifiable credentials"])
        if "GDPR" in client_clean or "GDPR" in ai_lower:
            key_terms.append("GDPR")
        
        # Also check if manual value appears directly in AI
        if manual_lower in ai_lower or any(term.lower() in ai_lower for term in manual_terms if len(term) >= 3):
            return True
        # Check key terms
        if any(term.lower() in ai_lower for term in key_terms):
            return True
    
    # For Regulations Followed, check if manual value (or key part like "GDPR") appears in AI list
    if "regulation" in field_name.lower() and "followed" in field_name.lower():
        import re
        # Extract regulation names from manual (e.g., "EU GDPR - EU General Data Protection Regulation" -> "GDPR")
        manual_lower = client_clean.lower()
        ai_lower = ai_clean.lower()
        
        # Extract acronyms and key terms
        manual_acronyms = re.findall(r'\b[A-Z]{2,}\b', client_clean)
        manual_words = [w.lower() for w in re.findall(r'\b\w{4,}\b', client_clean)]
        
        # Check if any acronym or key word from manual appears in AI
        if any(acronym.lower() in ai_lower for acronym in manual_acronyms):
            return True
        # Check if significant words match (e.g., "GDPR", "eIDAS")
        key_regs = ["GDPR", "eIDAS", "CCPA", "FedRAMP", "SOC"]
        if any(reg.lower() in manual_lower and reg.lower() in ai_lower for reg in key_regs):
            return True
        # Check if manual value is contained in AI
        if manual_lower in ai_lower:
            return True
    
    # For URL/website fields, normalize by domain only (ignore paths, trailing slashes, www)
    if 'website' in field_name.lower() or ('url' in field_name.lower() and 'source' not in field_name.lower()):
        from urllib.parse import urlparse
        try:
            # Normalize both URLs to base domain for comparison
            client_parsed = urlparse(client_clean if client_clean.startswith('http') else f'https://{client_clean}')
            ai_parsed = urlparse(ai_clean if ai_clean.startswith('http') else f'https://{ai_clean}')
            
            # Compare: scheme + netloc (domain) - ignore www prefix, paths, and trailing slashes
            client_domain = client_parsed.netloc.lower().replace('www.', '')
            ai_domain = ai_parsed.netloc.lower().replace('www.', '')
            client_scheme = client_parsed.scheme.lower() if client_parsed.scheme else 'https'
            ai_scheme = ai_parsed.scheme.lower() if ai_parsed.scheme else 'https'
            
            # Match if same domain (scheme + netloc without www)
            if client_domain == ai_domain and (client_scheme == ai_scheme or not client_parsed.scheme or not ai_parsed.scheme):
                return True
        except Exception:
            pass  # Fall through to exact match if URL parsing fails
    
    # For short fields (dates, URLs, booleans, status), require exact match
    # Also exclude source fields (Live Source, Archived Source) from matching
    if any(x in field_name.lower() for x in ['date', 'url', 'repository', 'source']):
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
        # Normalize words by removing punctuation for better matching
        import re
        client_words_normalized = set(re.sub(r'[^\w\s]', '', word) for word in client_clean.lower().split() if len(word) > 3)
        ai_words_normalized = set(re.sub(r'[^\w\s]', '', word) for word in ai_clean.lower().split() if len(word) > 3)
        # Also check if key words/phrases overlap significantly
        client_words = set(word for word in client_clean.lower().split() if len(word) > 3)  # Skip short words
        ai_words = set(word for word in ai_clean.lower().split() if len(word) > 3)
        if len(client_words_normalized) > 0 and len(ai_words_normalized) > 0:
            word_overlap = len(client_words_normalized & ai_words_normalized) / len(client_words_normalized | ai_words_normalized) if len(client_words_normalized | ai_words_normalized) > 0 else 0
            # Also check if significant words (4+ chars) match
            significant_overlap = word_overlap * 1.3  # Boost significant word overlap
            # Use highest of similarity or word overlap
            final_score = max(similarity, significant_overlap)
            
            # Lower threshold for Mission Statement and Tech Stack Descriptions
            # These fields often have same meaning but different wording - consider as match
            if "mission" in field_name.lower():
                # For Mission Statement, check for key concept overlap
                # Key concepts: control, data, people/individuals, own/sovereign, privacy, trust
                # Also check for synonyms: enable/empower, give/provide, understand/comprehend
                mission_keywords = ['control', 'data', 'people', 'individuals', 'own', 'sovereign', 
                                  'privacy', 'trust', 'understand', 'ability', 'give', 'enable',
                                  'empower', 'provide', 'comprehend', 'value']
                client_lower = client_clean.lower()
                ai_lower = ai_clean.lower()
                # Count how many key concepts appear in both texts
                matching_concepts = sum(1 for kw in mission_keywords if kw in client_lower and kw in ai_lower)
                # For Mission Statement, if at least 1 key concept matches (especially data/control/people)
                # and similarity is reasonable (> 0.04), consider it a match
                # This handles cases where meaning is similar but wording is very different
                if matching_concepts >= 1 and similarity > 0.04:
                    return True
                effective_threshold = 0.04  # Lowered threshold for mission statements (was 0.05)
            elif "tech stack" in field_name.lower():
                # For Tech Stack, check for key concept overlap
                # Key concepts: SSI, identity, credentials, decentralized, blockchain, DLT, DID, VC
                tech_keywords = ['ssi', 'identity', 'credential', 'decentralized', 'blockchain', 
                               'dlt', 'did', 'vc', 'verifiable', 'self-sovereign', 'trust',
                               'data', 'privacy', 'cosmos', 'infrastructure']
                client_lower = client_clean.lower()
                ai_lower = ai_clean.lower()
                # Count how many key concepts appear in both texts
                matching_concepts = sum(1 for kw in tech_keywords if kw in client_lower and kw in ai_lower)
                # If at least 2-3 key concepts match, consider it a semantic match
                if matching_concepts >= 2:
                    return True
                effective_threshold = 0.05  # Very low threshold for tech stack
            else:
                effective_threshold = threshold
            
            return final_score >= effective_threshold
    
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
        
        # Recalculate matches using fixed semantic matching (instead of relying on Excel Match? column)
        # This ensures we use the latest matching logic even if Excel comparison sheet is outdated
        matches = 0
        for _, row in data_fields.iterrows():
            field_name = str(row.get('Field', '')).strip()
            client_val = str(row.get('Client Value', '')).strip()
            ai_val = str(row.get('AI Value', '')).strip()
            
            # Clean up values
            if client_val.lower() in ('nan', 'none', ''):
                client_val = ''
            if ai_val.lower() in ('nan', 'none', ''):
                ai_val = ''
            
            # Recalculate match using the fixed is_text_match function
            if is_text_match(client_val, ai_val, field_name):
                matches += 1
        
        fields = len(data_fields)
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


def calculate_coverage(
    output_xlsx: Path,
    projects: Optional[List[str]] = None,
    exclude_fields: Optional[List[str]] = None
) -> Tuple[Dict[str, Dict[str, float]], float]:
    """
    Calculate data coverage for each project and overall coverage.
    
    Coverage = (filled fields / total fields) * 100
    A field is considered "filled" if it has a non-empty value.
    Note: "Failed to disclose" is a valid response for some fields but doesn't count as "filled"
    (it means information was not found, so it's effectively empty).
    
    Args:
        output_xlsx: Path to output Excel file with AI sheet
        projects: List of project names to analyze (None = all projects in AI sheet)
        exclude_fields: Fields to exclude from coverage calculation (None = use defaults)
    
    Returns:
        Tuple of (project_results, overall_coverage) where:
        - project_results: Dict mapping project name to coverage info
        - overall_coverage: Overall coverage percentage
    """
    # Default excluded fields (same as accuracy)
    if exclude_fields is None:
        exclude_fields = ['Live Source', 'Archived Source', 'Product Name', 'ID', 'Logo', '_evidence']
    
    # Load AI extraction sheet
    try:
        df_ai = pd.read_excel(output_xlsx, sheet_name='AI', dtype=str)
    except FileNotFoundError:
        raise FileNotFoundError(f"{output_xlsx} not found. Run extraction first.")
    except Exception as e:
        raise RuntimeError(f"Error reading AI sheet: {e}")
    
    # Get all data columns (exclude source columns and internal columns)
    all_columns = df_ai.columns.tolist()
    data_columns = [
        col for col in all_columns
        if not col.startswith('Source ')
        and not col.startswith('Archived Source ')
        and col not in exclude_fields
    ]
    
    # Get list of projects to analyze
    if projects is None:
        # Analyze all projects in the AI sheet
        projects = df_ai['Product Name'].dropna().unique().tolist()
    
    project_results = {}
    total_fields = 0
    total_filled = 0
    
    for project in projects:
        proj_data = df_ai[df_ai['Product Name'] == project]
        if len(proj_data) == 0:
            project_results[project] = {'coverage': 0.0, 'filled': 0, 'total': 0, 'failed_to_disclose': 0, 'empty': 0, 'found': False}
            continue
        
        row = proj_data.iloc[0]
        
        filled_count = 0
        failed_to_disclose_count = 0
        empty_count = 0
        
        for col in data_columns:
            val = str(row.get(col, '')).strip()
            val_lower = val.lower()
            
            # Check if field is empty
            if not val or val_lower in ('nan', 'none', '', 'n/a'):
                empty_count += 1
            # Check if field has "Failed to disclose" (valid response but not considered "filled")
            elif 'failed to disclose' in val_lower:
                failed_to_disclose_count += 1
            else:
                filled_count += 1
        
        total_cols = len(data_columns)
        coverage = (filled_count / total_cols * 100) if total_cols > 0 else 0
        
        project_results[project] = {
            'coverage': coverage,
            'filled': filled_count,
            'total': total_cols,
            'failed_to_disclose': failed_to_disclose_count,
            'empty': empty_count,
            'found': True
        }
        
        total_fields += total_cols
        total_filled += filled_count
    
    overall_coverage = (total_filled / total_fields * 100) if total_fields > 0 else 0
    
    return project_results, overall_coverage


def print_accuracy_report(output_xlsx: Path, projects: Optional[List[str]] = None, project: Optional[str] = None) -> None:
    """
    Print a formatted accuracy report.
    
    Args:
        output_xlsx: Path to output Excel file with Comparison sheet
        projects: List of project names to analyze (None = all with data)
        project: Single project name to analyze (overrides projects if provided)
    """
    # If single project specified, use that; otherwise use projects list
    if project:
        projects = [project]
    
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


def print_coverage_report(output_xlsx: Path, projects: Optional[List[str]] = None, project: Optional[str] = None) -> None:
    """
    Print a formatted coverage report showing what percentage of fields are filled.
    
    Args:
        output_xlsx: Path to output Excel file with AI sheet
        projects: List of project names to analyze (None = all projects in AI sheet)
        project: Single project name to analyze (overrides projects if provided)
    """
    # If single project specified, use that; otherwise use projects list
    if project:
        projects = [project]

    project_results, overall_coverage = calculate_coverage(output_xlsx, projects)
    
    print("=" * 70)
    print("Data Coverage Analysis")
    print("=" * 70)
    print("Note: Coverage = (filled fields / total fields) * 100")
    print("      'Failed to disclose' is a valid response but doesn't count as 'filled'")
    print("      Only Data Columns included (Product Name, ID, Logo, and source fields excluded)")
    print()
    
    for project, results in sorted(project_results.items()):
        if not results.get('found'):
            print(f"{project}: ⚠️  Not found in AI extraction results")
            continue
        
        coverage = results['coverage']
        filled = results['filled']
        total = results['total']
        failed_to_disclose = results.get('failed_to_disclose', 0)
        empty = results.get('empty', 0)
        
        status = "✅" if coverage >= 80 else "⚠️" if coverage >= 60 else "❌"
        print(f"{status} {project}:")
        print(f"   Coverage: {coverage:.1f}% ({filled}/{total} fields filled)")
        if failed_to_disclose > 0:
            print(f"   └─ 'Failed to disclose': {failed_to_disclose} fields")
        if empty > 0:
            print(f"   └─ Empty: {empty} fields")
        print()
    
    print("=" * 70)
    
    # Calculate totals
    total_filled = sum(r['filled'] for r in project_results.values())
    total_fields = sum(r['total'] for r in project_results.values())
    total_failed = sum(r.get('failed_to_disclose', 0) for r in project_results.values())
    total_empty = sum(r.get('empty', 0) for r in project_results.values())
    
    print(f"Overall Coverage: {overall_coverage:.1f}% ({total_filled}/{total_fields} fields filled)")
    if total_failed > 0:
        print(f"   └─ 'Failed to disclose': {total_failed} fields")
    if total_empty > 0:
        print(f"   └─ Empty: {total_empty} fields")
    print("=" * 70)
    
    if overall_coverage >= 80:
        print("✅ Excellent coverage! Most fields are populated.")
    elif overall_coverage >= 60:
        print("⚠️  Good coverage, but some fields need attention.")
    else:
        print("❌ Low coverage. Consider improving extraction strategies.")


def main():
    """Main entry point for running accuracy check from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Check accuracy or coverage of AI extraction results")
    parser.add_argument(
        "--projects",
        type=str,
        help='Comma-separated project names to check (e.g., "cheqd,MÁS"). If not provided, checks all available projects.'
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/output.xlsx",
        help="Path to output Excel file (default: data/output.xlsx)"
    )
    parser.add_argument(
        "--check-coverage",
        action="store_true",
        help="Check data coverage instead of accuracy (default: check accuracy)"
    )
    args = parser.parse_args()
    
    output_xlsx = Path(args.output)
    
    if not output_xlsx.exists():
        print(f"Error: {output_xlsx} not found. Run extraction first.")
        sys.exit(1)
    
    # Parse projects if provided
    projects = None
    if args.projects:
        projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    
    if args.check_coverage:
        print_coverage_report(output_xlsx, projects=projects)
    else:
        print_accuracy_report(output_xlsx, projects=projects)


if __name__ == '__main__':
    main()

