"""
LLM-based data extraction from scraped web pages.

This module orchestrates the extraction of structured data using LLMs:
1. Context Packing: Prioritizes pages by relevance (homepage > about > docs > blog)
2. Prompt Engineering: Builds detailed prompts using codebook field definitions
3. LLM Extraction: Calls OpenAI/Ollama to extract structured data
4. Result Parsing: Handles JSON extraction with robust fallbacks
5. Evidence Tracking: Tracks source URLs for each extracted value

The extraction uses field definitions from the codebook to provide
specific guidance to the LLM on how to extract each field.
"""
from __future__ import annotations
import json
import logging
import os
import re
from typing import Any, Dict, List, Tuple, Optional
from urllib.parse import urlparse

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.schema import (
    name_header as _name_header,
    coerce_to_headers,
    normalize_status,
    normalize_fd,
    normalize_year,
)
from app.config.codebook import load_codebook, Codebook
from app.workers.llm_client import chat_json

log = logging.getLogger(__name__)


def _write_text(project: str, fname: str, text: str) -> None:
    # safe small dump into cache/<project>/
    from pathlib import Path
    from app import CACHE_DIR
    d = Path(CACHE_DIR) / project / "texts"
    d.parent.mkdir(parents=True, exist_ok=True)
    (Path(CACHE_DIR) / project / fname).write_text(text, encoding="utf-8")


def _parse_llm_json(raw: str) -> Tuple[Dict[str, Any] | None, str]:
    """
    Robustly parse JSON from LLM response with multiple fallback strategies.
    
    LLMs sometimes wrap JSON in tags or include extra text. This function
    tries multiple parsing strategies:
    1. Direct JSON parsing (if response is pure JSON)
    2. Extract from <JSON>...</JSON> tags (common LLM pattern)
    3. Extract outermost {...} braces (finds JSON object in text)
    
    Args:
        raw: Raw LLM response string (may contain JSON)
    
    Returns:
        Tuple[Dict[str, Any] | None, str]: 
            - Parsed dict or None if all strategies fail
            - Strategy name used ("json.loads", "xml-tag-salvage", "brace-salvage", or "failed")
    """
    if not raw:
        return None, "empty"
    # 1) exact
    try:
        obj = json.loads(raw)
        return (obj if isinstance(obj, dict) else None), "json.loads"
    except Exception:
        pass
    # 2) tagged
    m = re.search(r"<JSON>(.*?)</JSON>", raw, flags=re.DOTALL | re.IGNORECASE)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict):
                return obj, "xml-tag-salvage"
        except Exception:
            pass
    # 3) outermost braces
    s, e = raw.find("{"), raw.rfind("}")
    if s != -1 and e > s:
        try:
            obj = json.loads(raw[s : e + 1])
            if isinstance(obj, dict):
                return obj, "brace-salvage"
        except Exception:
            pass
    return None, "failed"


def _make_prompt_payload(project: str, headers: List[str], context: str, codebook: Codebook) -> str:
    """
    Build comprehensive LLM prompt with field-specific extraction guidance.
    
    Uses codebook field definitions to generate detailed instructions for each field.
    This helps the LLM understand exactly what to extract and where to find it.
    
    Args:
        project: Project name to extract data for
        headers: List of field names to extract
        context: Packed text content from scraped pages
        codebook: Codebook with field definitions and extraction guidance
    
    Returns:
        str: Complete prompt string for LLM
    """
    # Build field-specific extraction hints from codebook definitions
    field_guidance_lines = []
    for h in headers:
        # Skip Logo field - it's typically a URL to an image, not text to extract
        # Logo should be handled separately or excluded from extraction
        if h.strip().lower() == "logo":
            continue
        # First check if we have a field definition in codebook
        if h in codebook.field_definitions:
            field_def = codebook.field_definitions[h]
            extraction_guidance = field_def.get("extraction_guidance", "")
            description = field_def.get("description", "")
            field_type = field_def.get("type", "text")
            possible_values = field_def.get("possible_values", [])
            
            hint_parts = [f"- {h}:"]
            # Use extraction_guidance (Details column) as primary instruction - this tells exactly what to extract
            if extraction_guidance and extraction_guidance.strip():
                # Clean up newlines and make it readable
                guidance = extraction_guidance.strip().replace('\n', ' ')
                hint_parts.append(f"{guidance}")
            elif description:
                hint_parts.append(f"Definition: {description}")
                hint_parts.append("Extract from: official website, about pages, documentation")
            
            # Add specific hints based on field name patterns
            h_lower = h.lower()
            if "mission" in h_lower:
                hint_parts.append("CRITICAL: Extract the EXACT mission statement word-for-word from official sources. The mission can be SHORT (1-5 words like 'Enforcing Information Security') or LONG (full sentences). Look for: sentences starting with action verbs ('To give', 'To enable', 'Our mission is'), SHORT taglines/headings describing purpose, or full paragraphs explaining WHY (not WHAT). PRIORITIZE: homepage hero/taglines, page titles/headers, 'About Us' first paragraph, 'Mission' pages. Extract EXACT text - do NOT paraphrase, expand, translate, or add context. If mission is short (e.g., 'Enforcing Information Security'), extract exactly that - do NOT extract a longer description. AVOID: product features, company histories, job descriptions, wrong language text, marketing paragraphs. The mission is often in page headers, taglines, or the first sentence of About Us.")
            if "funding" in h_lower:
                hint_parts.append("CRITICAL: Look for investment announcements, funding rounds, investor mentions, or venture capital backing. Search: press releases, news articles, 'About Us' pages, 'Investors' page, 'Backers' page, Crunchbase links, or funding announcement blog posts. Extract the full investor/backer organization name (e.g., 'Outlier Ventures Operations Ltd (Outlier Ventures)'). For Private Sector Funding, look for venture capital firms, companies, foundations, consortia, or corporate sponsors.")
            if "partner" in h_lower or "affiliated" in h_lower:
                hint_parts.append("CRITICAL: Look for partner/ecosystem pages, collaboration announcements, or integration mentions. Check: 'Partners' page, 'Ecosystem' page, 'Integrations' page, 'Collaborations' page, homepage mentions, About Us, or press releases. Extract the full partner/entity name (including organization name if mentioned, e.g., 'Dock Labs AG (Dock)').")
            if "repository" in h_lower and "code" in h_lower:
                hint_parts.append("CRITICAL: Look for GitHub, GitLab, or other code repository links. Check: footer links (often at bottom of pages), 'View Source' buttons, developer pages, documentation pages, README files, 'Contribute' sections, 'Open Source' pages, or 'Developer Resources'. The URL format is: https://github.com/[org]/[repo] or https://gitlab.com/[org]/[repo]. Also search page text for mentions like 'source code available at', 'repository', 'github.com/[org]'. Extract the FULL repository URL including https://.")
            if "politically" in h_lower or "government" in h_lower:
                hint_parts.append("CRITICAL: Look for ANY mention of government, state, or political entity involvement. Check: 'Government' section, 'Partners' page, press releases, 'Use Cases', case studies, news articles. Look for: 'government partnership', 'government project', 'used by government', 'government adoption', 'government entity', 'state-affiliated', 'endorsed by government', 'government funding', 'government contract', 'government case study', 'government use case', 'Citizens & Governments', 'government agency', 'government clients', 'government services', 'eIDAS', 'European regulations', 'government programmes'. If ANY government entity is mentioned as partner, user, client, involved in use cases, or supporting regulations like eIDAS, use 'True'. Only use 'False' if explicitly stated as private-sector only with no government involvement whatsoever.")
            if "managing entity" in h_lower or ("managing" in h_lower and "entity" in h_lower):
                hint_parts.append("CRITICAL: Look for the organization that manages/runs the project. Check: 'About Us' page (often mentions the foundation, company, or organization behind the project), footer (may show '© [Organization Name]'), legal pages, 'Who We Are' pages, company registration pages, or terms of service. Search for: 'Foundation', 'Limited', 'Inc', 'Corp', 'LLC', 'Ltd', company registration number, or legal entity name. Extract the FULL legal entity name if available (e.g., 'Cheqd Foundation Limited (Cheqd)' or just 'Cheqd Foundation Limited' if that's what's stated). If only a company name is mentioned without legal suffix, include it. If multiple names are given (legal name and common name), use format: 'Legal Name (Common Name)'.")
            if "app store" in h_lower or "app" in h_lower and "store" in h_lower:
                hint_parts.append("CRITICAL: Look for app store links or application download links. Check: 'Download' page, 'Get Started' page, footer links, product pages, or developer pages. Look for URLs like 'https://creds.xyz', 'https://apps.apple.com', 'https://play.google.com', or direct application URLs. Extract the full URL.")
            if "exportable" in h_lower and "credential" in h_lower:
                hint_parts.append("CRITICAL: Look for explicit mentions of credential export functionality. Search for: 'export credentials', 'download credentials', 'backup credentials', 'export wallet', or documentation about credential management. If the project mentions users can export/download their credentials, use 'True'. If it only mentions import but not export, use 'False'. If no information found, use 'Failed to disclose'.")
            if "blockchain" in h_lower and ("registr" in h_lower or "data" in h_lower):
                hint_parts.append("CRITICAL: Look for which blockchain or verifiable data registry the project uses. Check: technical documentation, architecture docs, 'Technology' page, developer docs, or whitepapers. Look for mentions of specific blockchains like 'Cheqd', 'Hyperledger Indy', 'Ethereum', 'Polygon', or registry names. Extract the specific blockchain/registry name.")
            if "tech stack" in h_lower or "technology" in h_lower:
                hint_parts.append("Extract detailed technology descriptions from technical documentation, developer pages, architecture docs, or product pages.")
            
            # Determine if this field allows "Failed to disclose"
            # Only specific fields explicitly allow it per data definitions
            allows_failed_to_disclose = False
            extraction_guidance_lower = extraction_guidance.lower() if extraction_guidance else ""
            if "failed to disclose" in extraction_guidance_lower or "Failed to disclose" in extraction_guidance:
                allows_failed_to_disclose = True
            
            # Add type-specific instructions with proper constraints
            if field_type == "boolean" or field_type == "ternary":
                # Boolean/ternary fields can use ternary_enums (which includes "Failed to disclose")
                hint_parts.append(f"Must be one of: {codebook.ternary_enums}")
            elif field_type == "status":
                hint_parts.append(f"Must be one of: {codebook.status_enums}")
            elif field_type == "url":
                # URL fields: use empty string if not found, NEVER "Failed to disclose"
                if not allows_failed_to_disclose:
                    hint_parts.append("CRITICAL: If URL not found, use empty string (\"\"). Do NOT use 'Failed to disclose'.")
            elif field_type == "year" or "date" in h.lower():
                if "announcement" in h.lower():
                    hint_parts.append("CRITICAL: Extract the FIRST/EARLIEST announcement year (YYYY format, 4 digits only). Search ALL pages for: 'founded in [YEAR]', 'established in [YEAR]', 'announced in [YEAR]', 'company history', 'our story', 'project announcement', 'first introduced'. Check the oldest blog posts, press releases, or company timeline pages. FILTER OUT: dates before 2000 (likely unrelated company history), dates after current year (future dates), copyright years, page last-updated dates. Use the original project/company founding/announcement date. If you see 'founded 2021' and 'announced features in 2023', use 2021. If no valid project-related date found (only unrelated old dates), use empty string.")
                elif "launch" in h.lower():
                    hint_parts.append("CRITICAL: Extract the FIRST/EARLIEST launch year (YYYY format, 4 digits only). Look for when the product FIRST became available. Search for: 'launched in [YEAR]', 'went live in [YEAR]', 'beta release [YEAR]', 'first version [YEAR]', 'general availability [YEAR]', 'public launch [YEAR]', 'product launch'. FILTER OUT: dates before 2000 (likely unrelated), dates after current year (future dates - invalid), copyright years, blog post publication dates. DO NOT use recent launch dates for new features - use the original product launch. If you see 'launched 2021' and 'new features launched 2023', use 2021. If no valid project launch date found, use empty string.")
                else:
                    hint_parts.append("Extract as 4-digit year (YYYY format). Look for the earliest date mentioned in company history, founding dates, or first announcements. FILTER OUT dates before 2000 or after current year.")
                # Year fields: use empty string if not found, NEVER "Failed to disclose"
                if not allows_failed_to_disclose:
                    hint_parts.append("CRITICAL: If year not found or only invalid dates found (before 2000, after current year), use empty string (\"\"). Do NOT use 'Failed to disclose'.")
            elif field_type == "text":
                # Text fields: only allow "Failed to disclose" if explicitly stated in extraction_guidance
                if not allows_failed_to_disclose:
                    hint_parts.append("CRITICAL: If information not found, use empty string (\"\"). Do NOT use 'Failed to disclose' - this field does not allow it per data definitions.")
            
            if possible_values:
                hint_parts.append(f"Valid values: {', '.join(possible_values)}")
            
            if len(hint_parts) > 1:  # More than just the field name
                field_guidance_lines.append(" ".join(hint_parts))
            continue
    
    # If no codebook definitions, use fallback guidance
    if not field_guidance_lines:
        field_guidance_lines = [
            "- Mission Statement: Extract the COMPLETE mission statement from official sources. Look in 'About Us', 'Mission', 'What We Do', FAQ pages, or homepage hero sections. Extract the full statement, not partial text. Prioritize official company descriptions over blog content.",
            "- Status: Use ONLY these values: {status_enums}. Look for 'launched', 'live', 'in production', 'general availability (GA)', 'pilot', 'beta', 'announced'. Check homepage, product pages, announcements.",
            "- Tech Stack: Extract detailed technology descriptions from technical docs, developer pages, architecture docs.",
            "- Dates: Extract as 4-digit year (YYYY). Search press releases, blog announcements, news articles.",
            "- SSI Technology: Look for explicit mentions of SSI, self-sovereign identity, decentralized identity, DIDs, VCs.",
            "- ZKP: Look for mentions of zero-knowledge proofs, zk-SNARKs, zk-STARKs, privacy-preserving proofs.",
            "- Credential Storage: Look for wallet storage, key management, user-controlled keys, cloud storage, HSM.",
            "- Funding: Search for 'funding', 'investment', 'raised', 'series', 'seed round', 'venture capital', 'VC', 'investors', 'backers' in news, press releases, 'About Us', blog posts, and investor relations pages. Extract specific funding amounts, rounds (Seed, Series A/B/C), investor names, and dates when mentioned.",
            "- Partners/Affiliated Entities: Look for 'partners', 'partnerships', 'affiliated', 'collaboration', 'alliance', 'integrated with', 'works with' in homepage, 'About Us', 'Partners', 'Ecosystem', or press release pages. Extract partner/entity names when explicitly listed.",
            "- Regulations: Look for GDPR, eIDAS, PSD2, ISO standards, SOC 2 mentions.",
            "- Standards/Protocols: Look for W3C (DID, VC, OAuth, OpenID), ISO standards, blockchain protocols.",
        ]
    
    field_guidance = "\n".join(field_guidance_lines)
    
    instructions = (
        "You are extracting structured data about a digital identity project from web sources.\n"
        "Return ONLY one JSON object enclosed in <JSON>...</JSON>.\n\n"
        "CRITICAL RULES:\n"
        "1. Set 'Product Name' exactly to: `{project_name}`\n"
        "2. Website field: Extract the main/official website URL from the context. Look for the homepage URL.\n"
        "3. Mission Statement: Extract the COMPLETE mission statement from official sources. "
        "CRITICAL EXTRACTION RULES:\n"
        "   - The mission describes WHY the project exists (purpose), not WHAT it does (features)\n"
        "   - Prioritize homepage hero sections, 'About Us', 'Mission', 'What We Do', FAQ pages\n"
        "   - Look for sentences starting with action verbs: 'To give', 'To enable', 'To provide', 'Our mission is', 'We aim to', 'We give'\n"
        "   - Extract the FULL sentence or paragraph that expresses the core purpose - NOT marketing descriptions\n"
        "   - AVOID extracting: 'cheqd is a market-leading...', 'we provide...', 'our platform enables...' (these are WHAT, not WHY)\n"
        "   - DO extract: 'To give people...', 'To enable individuals...', 'Our mission is to...' (these describe WHY)\n"
        "   - If multiple mission statements exist, choose the one that best describes the underlying purpose/mission\n"
        "   - Search ALL provided pages thoroughly, especially About Us and Mission pages\n"
        "4. Status: Use ONLY these values: {status_enums}. Look for words like 'launched', 'pilot', 'announced', 'live', 'production'.\n"
        "5. Ternary fields (yes/no questions): Must be one of: {ternary}. Look for explicit mentions.\n"
        "   CRITICAL FOR 'Politically Involved?' FIELD:\n"
        "   - Scan the ENTIRE context for ANY mention of: 'government', 'state', 'public sector', 'Citizens & Governments', 'government agency', 'government clients', 'eIDAS', 'European regulations', 'government services', 'government programmes', 'national ID', 'public services'\n"
        "   - If you find ANY of these terms OR see government listed as a use case/client/partner, set this field to 'True'\n"
        "   - Examples that indicate 'True': 'Citizens & Governments' in use cases, 'Government agency' in partner list, mentions of eIDAS (European government regulation), 'government services' sector\n"
        "   - Only use 'False' if you search thoroughly and find NO government-related mentions anywhere\n"
        "6. Dates: Extract 4-digit years (YYYY format). CRITICAL RULES FOR DATES:\n"
        "   - For 'Announcement Date': Search for the FIRST time the project was publicly announced\n"
        "     Look for: 'founded in', 'established', 'announced', 'first introduced', company founding date\n"
        "     Check the OLDEST blog posts, press releases, or company history pages\n"
        "   - For 'Launch Date': Search for when the product FIRST became available to users\n"
        "     Look for: 'launched', 'went live', 'beta release', 'first version', 'general availability (GA)', 'public release'\n"
        "   - IMPORTANT: Do NOT use:\n"
        "     * Recent blog post dates (these are when posts were published, not launch dates)\n"
        "     * Feature announcement dates (these are when features were added, not the original launch)\n"
        "     * Conference presentation dates\n"
        "   - If you find multiple dates, use the EARLIEST one\n"
        "   - If the date is explicitly stated (e.g., 'founded in 2021'), use that exact year\n"
        "7. Technology fields: Look for mentions of SSI, DLT, blockchain, verifiable credentials, ZKP (zero-knowledge proofs).\n"
        "8. When information is NOT found in the context after careful search, use 'Failed to disclose'.\n"
        "9. Every extracted value (non-empty field) MUST include an evidence entry in _evidence array with:\n"
        "   - field: the field name\n"
        "   - value: the extracted value\n"
        "   - source_url: the URL where you found it\n"
        "   - source_type: 'webpage'\n"
        "   - confidence: 'high', 'medium', or 'low'\n\n"
        "EXTRACTION STRATEGY:\n"
        "1. Start with homepage URLs - they often contain mission, status, overview\n"
        "2. Check 'About Us' / 'Mission' pages for company descriptions\n"
        "3. Review technical documentation for technology fields\n"
        "4. Search blog/news for dates and announcements\n"
        "5. Look for explicit mentions rather than inferring\n"
        "6. For dates: Search for announcement/launch dates in blog posts, press releases, or company history. "
        "Extract the year when the project was first announced or launched. Look for explicit date mentions.\n"
        "7. For mission statements: Check FAQ pages, About Us pages, and homepage before blog content\n"
        "8. For code repositories: Look for 'GitHub', 'GitLab', 'code repository', or 'source code' mentions\n\n"
        "FIELD-SPECIFIC GUIDANCE:\n{field_guidance}\n"
        "IMPORTANT: Search the ENTIRE context thoroughly. Official website content is most reliable.\n"
        "CRITICAL VALUE CONSTRAINTS - READ CAREFULLY:\n"
        "ONLY these 6 fields allow 'Failed to disclose' as a value:\n"
        "  1. Uses/endorses ZKP\n"
        "  2. Has Exportable Credentials\n"
        "  3. Credential And Key Storage\n"
        "  4. Targets Holders\n"
        "  5. Targets Issuers\n"
        "  6. Targets Verifiers\n"
        "\n"
        "FOR ALL OTHER FIELDS:\n"
        "- If information is NOT found, use empty string (\"\") NOT 'Failed to disclose'\n"
        "- Examples of fields that MUST use empty string if not found:\n"
        "  * Logo, Mission Statement, Website, Public Code Repository → empty string if not found\n"
        "  * Affiliated Entity / Partner, Private Sector Funding, Managing Entity → empty string if not found\n"
        "  * App Store Link, Blockchains / Verifiable Data Registries → empty string if not found\n"
        "  * ALL date fields, ALL URL fields, ALL text fields (except the 6 above) → empty string if not found\n"
        "\n"
        "FIELD TYPE CONSTRAINTS:\n"
        "- Boolean/ternary fields: Must be one of {ternary} only (True, False, or Failed to disclose)\n"
        "- Status fields: Must be one of {status_enums} only (Announced, Pilot, Launched, Discontinued)\n"
        "- URL fields: Extract full URL or use empty string, NEVER 'Failed to disclose'\n"
        "- Year/Date fields: Extract YYYY year or use empty string, NEVER 'Failed to disclose'\n"
        "- Regular text fields: Extract actual text value or use empty string, NEVER 'Failed to disclose' (unless it's one of the 6 allowed fields)\n"
        "\n"
        "Extract real values when found. Follow the value constraints STRICTLY. Using 'Failed to disclose' in wrong fields will be rejected.\n"
    ).replace("{project_name}", project)\
     .replace("{status_enums}", str(codebook.status_enums))\
     .replace("{ternary}", str(codebook.ternary_enums))\
     .replace("{field_guidance}", field_guidance)

    payload = {
        "project": project,
        "headers": headers,
        "instructions": instructions,
        "context": context,
        "normalize": codebook.normalize,         # may be used by the model (soft hint)
        "field_synonyms": codebook.field_synonyms,  # idem
    }
    return json.dumps(payload, ensure_ascii=False)


def _extract_official_website(pages: List[Dict[str, Any]], known_website: str = "") -> str:
    """Try to find the official website from pages - prioritize known website, then homepage URLs"""
    # If we have a known website, check if it's in the pages and return it
    if known_website:
        parsed_known = urlparse(known_website if known_website.startswith("http") else f"https://{known_website}")
        known_domain = parsed_known.netloc.lower().replace('www.', '')
        known_base = f"{parsed_known.scheme or 'https'}://{known_domain}/"
        
        for p in pages:
            url = (p.get("url") or "").strip()
            if not url:
                continue
            parsed = urlparse(url)
            url_domain = parsed.netloc.lower().replace('www.', '')
            # Check if known website domain matches
            if known_domain in url_domain or url_domain in known_domain:
                # Return homepage version
                return known_base
    
    candidates = []
    for p in pages[:15]:  # Check first 15 pages
        url = (p.get("url") or "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        path = parsed.path.lower()
        
        # Skip non-official sources
        if any(skip in domain for skip in [
            'youtube.com', 'facebook.com', 'twitter.com', 'x.com',
            '.reviews', 'linkedin.com', 'reddit.com', 'archive.', 'podcasts.apple.com'
        ]):
            continue
        
        # Highest priority: homepage (no path or minimal path)
        if url.count('/') <= 3:
            return url
            
        # Official info pages
        if any(x in path for x in ['/about', '/company', '/home']):
            candidates.append((url, 1))
        elif 'github.com' in domain:
            candidates.append((url, 2))
    
    # Return highest priority candidate or first page URL
    if candidates:
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    
    # Fallback: return first valid URL
    for p in pages:
        url = (p.get("url") or "").strip()
        if url and url.count('/') <= 5:  # Not too deep
            parsed = urlparse(url)
            if not any(skip in parsed.netloc.lower() for skip in ['youtube.com', 'facebook.com', 'twitter.com']):
                return url
    return ""

def _seed_row(project: str, headers: List[str], pages: List[Dict[str, Any]], known_website: str = "") -> Dict[str, Any]:
    data: Dict[str, Any] = {h: "" for h in headers}
    nh = _name_header(headers)
    data[nh] = project
    data["_evidence"] = []
    if pages:
        # Better website extraction - use known website if available
        official_url = _extract_official_website(pages, known_website=known_website)
        if not official_url:
            first = pages[0]
            official_url = (first.get("url") or "").strip()
        
        # Website field
        for h in headers:
            if h.lower() == "website" and official_url:
                # Clean up URL - remove fragments, normalize
                parsed = urlparse(official_url)
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip('/')
                if not clean_url.endswith(('.html', '.php', '.asp')):
                    clean_url = clean_url.rstrip('/')
                data[h] = clean_url
                data["_evidence"].append(
                    {
                        "field": "Website",
                        "value": clean_url,
                        "source_url": official_url,
                        "source_type": "webpage",
                        "confidence": "high" if official_url.count('/') <= 3 else "medium",
                    }
                )
                break
        # Brief description if any matching column exists
        first_page_text = pages[0].get("text", "") if pages else ""
        desc = (first_page_text[:240] + "…") if len(first_page_text) > 240 else first_page_text
        for h in headers:
            if h.lower() in ("description", "project description", "brief"):
                data[h] = desc
                if official_url:
                    data["_evidence"].append(
                        {
                            "field": h,
                            "value": desc,
                            "source_url": official_url,
                            "source_type": "webpage",
                            "confidence": "low",
                        }
                    )
                break
    return data


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def extract_record(
    *,
    project: str,
    headers: List[str],
    pages: List[Dict[str, Any]],
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    max_output_tokens: int = 4000,
    codebook: Optional[Codebook] = None,
    known_website: str = "",
) -> Dict[str, Any]:
    """
    Extract structured data from scraped pages using LLM.
    
    Process:
    1. Filter and prioritize pages (homepage > about > docs > blog)
    2. Pack context from top 20 pages (up to 10K chars each)
    3. Build detailed prompt with field-specific guidance
    4. Call LLM to extract structured JSON
    5. Parse and validate LLM response
    6. Coerce to match exact Excel headers
    7. Add seed data (website URL from known_website or first page)
    
    Args:
        project: Project name
        headers: List of Excel column headers to extract
        pages: List of scraped page dicts (url, text, status, mime)
        provider: LLM provider ('openai' or 'ollama')
        model: Model name (e.g., 'gpt-4o-mini', 'llama3.1')
        max_output_tokens: Maximum tokens for LLM response
        codebook: Optional codebook (loads default if None)
        known_website: Optional known website URL (helps identify official site)
    
    Returns:
        Dict[str, Any]: Extracted data dict keyed by headers, with _evidence list
    
    Note:
        Automatically retries up to 3 times on LLM failures.
    """
    codebook = codebook or load_codebook()
    log.info("[extract] %s: pages received=%d", project, len(pages))

    # ---- context packing: filter and prioritize high-quality content
    # Strategy: prioritize pages by information density and relevance
    # CRITICAL: Filter out low-quality pages (job postings, careers, downloads, etc.)
    page_candidates: List[Tuple[float, str, str]] = []  # (priority, url, text)
    
    # Keywords that indicate low-quality pages to filter out
    exclude_keywords = [
        'career', 'jobs', 'hiring', 'recruit', 'position', 'vacancy', 'apply now',
        'download', '.pdf', '.zip', '.exe', '.dmg',
        'cookie policy', 'privacy policy', 'terms of service', 'legal notice',
        'sitemap', 'robots.txt'
    ]
    
    for p in pages:
        url = (p.get("url") or "").lower()
        text = (p.get("text") or "")[:200].lower()  # Check first 200 chars
        
        # Skip pages with exclude keywords in URL or text
        should_skip = False
        for keyword in exclude_keywords:
            if keyword in url or keyword in text:
                should_skip = True
                log.debug("[extract] %s: Skipping page with '%s': %s", project, keyword, url[:80])
                break
        
        if should_skip:
            continue
        t = (p.get("text") or "").strip()
        u = (p.get("url") or "").strip()
        
        # Skip 404s, empty content, and very short pages
        if not t or len(t) < 100:
            continue
        
        # Skip error pages and placeholder pages
        t_lower = t.lower()
        u_lower = u.lower()
        if any(err in t_lower for err in ["404", "not found", "oops!", "page not found", 
                                          "this page doesn't exist", "for sale", "domain for sale"]):
            continue
        
        # Calculate priority score (lower = higher priority)
        priority = 5  # default
        
        # Highest priority: official homepage (no path or minimal)
        # Homepage often has mission statement in hero section
        if u.count('/') <= 3:
            priority = 0
        
        # Very high priority: About/Company/Mission pages (mission statements usually here)
        elif any(x in u_lower for x in ['/about', '/company', '/mission', '/who-we-are', '/what-we-do', '/our-story']):
            priority = 0.5
        
        # High priority: FAQ pages (often contain mission statements and company info)
        elif '/faq' in u_lower:
            priority = 1
        # High priority: Documentation (for tech fields)
        elif any(x in u_lower for x in ['/docs', '/documentation', '/developers', '/developer']):
            priority = 2
        # Medium: Blog/News (may have announcements)
        elif any(x in u_lower for x in ['/blog', '/news', '/press', '/articles']):
            priority = 3
        # Lower priority: everything else
        else:
            priority = 4
        
        # Boost priority for longer, more informative pages (but cap text length for speed)
        if len(t) > 2000:
            priority -= 0.5  # Slight boost for longer content
        
        # Use up to 6000 chars per page (reduced from 10000 for faster LLM processing)
        page_candidates.append((priority, u, t[:6000]))
    
    # Sort by priority and select top pages
    page_candidates.sort(key=lambda x: x[0])
    snippets: List[str] = []
    max_snippets = 15  # Balanced for speed and quality (reduced from 20)
    for priority, u, t in page_candidates[:max_snippets]:
        snippets.append(f"[URL]{u}\n{t}")
    
    log.info("[extract] %s: using %d page snippets (prioritized)", project, len(snippets))

    # Seeds (baseline)
    seeds = _seed_row(project, headers, pages, known_website=known_website)

    if not snippets:
        log.info("[extract] %s: no context → returning seeds only", project)
        return seeds

    context = "\n\n".join(snippets)
    user_payload = _make_prompt_payload(project, headers, context, codebook)

    messages = [
        {"role": "system", "content": (
            "You are a precise data extraction assistant specializing in digital identity and SSI projects. "
            "You extract structured information from web sources. "
            "You must ONLY use information explicitly found in the provided context. "
            "Be thorough - search all provided URLs and text. "
            "CRITICAL: Only 6-7 specific fields allow 'Failed to disclose' as a value. "
            "For all other fields, use empty string (\"\") if information is not found. "
            "Follow the field-specific value constraints in the user prompt exactly."
        )},
        {"role": "user", "content": user_payload},
    ]

    log.info("[extract] %s: calling LLM… (provider=%s, model=%s)", project, provider, model)
    try:
        raw = chat_json(messages=messages, provider=provider, model=model, max_tokens=max_output_tokens) or ""
    except Exception as e:
        err_txt = f"{type(e).__name__}: {e}"
        log.error("[extract] %s: LLM call failed → %s", project, err_txt)
        _write_text(project, "llm_error.txt", err_txt)
        raw = ""

    _write_text(project, "llm_raw.json", raw)
    log.info("[extract] %s: raw chars=%d", project, len(raw))

    obj, how = _parse_llm_json(raw)
    if not obj:
        log.info("[map] %s: parse failed (%s); using seeds only", project, how)
        data = seeds
    else:
        log.info("[map] %s: parse ok via %s; keys=%s", project, how, list(obj.keys())[:12])
        
        # Normalize to schema headers first
        llm_norm = coerce_to_headers(obj, headers, project_name=project) or {}
        seeds_norm = coerce_to_headers(seeds, headers, project_name=project) or {}
        
        # Post-process: Remove "Failed to disclose" from fields that don't allow it (after coercion to match exact headers)
        # Only these 6 fields are allowed to have "Failed to disclose" per data definitions
        allowed_failed_to_disclose_fields = {
            "Uses/endorses ZKP",
            "Has Exportable Credentials",
            "Credential And Key Storage",
            "Targets Holders",
            "Targets Issuers",
            "Targets Verifiers",
        }
        
        # Clean up "Failed to disclose" from normalized headers
        cleaned_count = 0
        for h in headers:
            val = str(llm_norm.get(h, "")).strip()
            if val.lower() == "failed to disclose" and h not in allowed_failed_to_disclose_fields:
                llm_norm[h] = ""  # Replace with empty string
                cleaned_count += 1
                log.debug("[extract] %s: Removed invalid 'Failed to disclose' from field '%s'", project, h)
        
        if cleaned_count > 0:
            log.info("[extract] %s: Cleaned %d invalid 'Failed to disclose' values", project, cleaned_count)
        
        # Merge: prefer LLM where seeds are empty
        data = {h: seeds_norm.get(h, "") for h in headers}
        mapped = 0
        for h in headers:
            sv = seeds_norm.get(h, "")
            lv = llm_norm.get(h, "")
            if (isinstance(sv, str) and not sv.strip()) and (isinstance(lv, str) and lv.strip()):
                data[h] = lv
                mapped += 1
        log.info("[map] %s: mapped=%d", project, mapped)

        # Evidence merge: start with existing evidence from seeds and LLM
        ev: List[Dict[str, Any]] = []
        if isinstance(seeds_norm.get("_evidence"), list):
            ev += seeds_norm["_evidence"]
        if isinstance(llm_norm.get("_evidence"), list):
            ev += llm_norm["_evidence"]
        
        # CRITICAL: Generate evidence entries for ALL extracted fields that don't have evidence yet
        # This ensures source URLs are available for populating "Live Source [Field Name]" columns
        # Use the first/highest priority page URL as the source for fields missing evidence
        primary_source_url = ""
        if pages:
            # Get highest priority page URL (already sorted by priority)
            page_candidates_sorted = sorted(page_candidates, key=lambda x: x[0])
            if page_candidates_sorted:
                primary_source_url = page_candidates_sorted[0][1]  # URL from highest priority page
        
        # Track which fields already have evidence
        fields_with_evidence = {e.get("field", "") for e in ev if isinstance(e, dict)}
        
        # Add evidence for any extracted field that doesn't have it yet
        for h in headers:
            if h in fields_with_evidence:
                continue  # Already has evidence
            
            val = str(data.get(h, "")).strip()
            if val and val.lower() not in ("nan", "none", ""):
                # Use primary source URL, or try to find a relevant page URL
                source_url = primary_source_url
                if not source_url and pages:
                    source_url = pages[0].get("url", "")
                
                if source_url:
                    ev.append({
                        "field": h,
                        "value": val,
                        "source_url": source_url,
                        "source_type": "webpage",
                        "confidence": "high" if h == "Website" else "medium",
                    })
                    log.debug("[extract] %s: Added evidence for field '%s' from %s", project, h, source_url[:60])
        
        data["_evidence"] = ev

    # --------- normalizations ----------
    if "Status" in data:
        data["Status"] = normalize_status(data.get("Status"))
    for k in [
        "Endorses/Uses ZKP",
        "Has Exportable Credentials",
        "Credential and Key Storage",
        "Targets Holders",
        "Targets Issuers",
        "Targets Verifiers",
    ]:
        if k in data:
            data[k] = normalize_fd(data.get(k))
    for k in ["Announcement", "Launch", "Project Announcement Date", "Project Launch Date"]:
        if k in data:
            data[k] = normalize_year(data.get(k))

    # Ensure all headers present & correct name set
    nh = _name_header(headers)
    if not data.get(nh):
        data[nh] = project
    for h in headers:
        data.setdefault(h, "")

    filled = sum(1 for k, v in data.items() if k != "_evidence" and isinstance(v, str) and v.strip())
    log.info("[extract] %s: fields filled=%d", project, filled)
    return data
