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
            if "funding" in h_lower:
                hint_parts.append("Look for investment announcements, funding rounds, investor mentions, or venture capital backing.")
            if "partner" in h_lower or "affiliated" in h_lower:
                hint_parts.append("Look for partner/ecosystem pages, collaboration announcements, or integration mentions.")
            if "repository" in h_lower and "code" in h_lower:
                hint_parts.append("Look for GitHub, GitLab, or other code repository links. Check footer, developer pages, or documentation.")
            
            # Add type-specific instructions
            if field_type == "boolean" or field_type == "ternary":
                hint_parts.append(f"Must be one of: {codebook.ternary_enums}")
            elif field_type == "status":
                hint_parts.append(f"Must be one of: {codebook.status_enums}")
            elif field_type == "year" or "date" in h.lower():
                if "announcement" in h.lower():
                    hint_parts.append("Extract the announcement year (YYYY format). Look for when the project was first announced in blog posts, press releases, or company descriptions. Extract the actual year mentioned.")
                elif "launch" in h.lower():
                    hint_parts.append("Extract the launch year (YYYY format). Look for when the product first went live, beta launched, or first version released. Extract the actual year mentioned.")
                else:
                    hint_parts.append("Extract as 4-digit year (YYYY format). Look for first dates in blog posts, press releases, or company history.")
            
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
        "3. Mission Statement: Extract the complete mission statement from official sources. "
        "Prioritize FAQ pages, About Us pages, company descriptions on homepage. "
        "Look for phrases like 'our mission', 'we aim', 'our goal', 'to give', 'to provide'. "
        "Extract the FULL statement, not partial sentences.\n"
        "4. Status: Use ONLY these values: {status_enums}. Look for words like 'launched', 'pilot', 'announced', 'live', 'production'.\n"
        "5. Ternary fields (yes/no questions): Must be one of: {ternary}. Look for explicit mentions.\n"
        "6. Dates: Extract 4-digit years (YYYY format) from the context. Look for explicit year mentions "
        "in blog posts, press releases, announcements, or company descriptions.\n"
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
        "Extract real values when found. Use 'Failed to disclose' ONLY when information is genuinely absent.\n"
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
    page_candidates: List[Tuple[float, str, str]] = []  # (priority, url, text)
    
    for p in pages:
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
        if u.count('/') <= 3:
            priority = 0
        # Very high priority: FAQ pages (often contain mission statements)
        elif '/faq' in u_lower:
            priority = 0.5
        # High priority: About/Company/Mission pages
        elif any(x in u_lower for x in ['/about', '/company', '/mission', '/who-we-are', '/what-we-do', '/our-story']):
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
        
        # Boost priority for longer, more informative pages
        if len(t) > 2000:
            priority -= 0.5  # Slight boost for longer content
        
        page_candidates.append((priority, u, t[:8000]))
    
    # Sort by priority and select top pages
    page_candidates.sort(key=lambda x: x[0])
    snippets: List[str] = []
    max_snippets = 20  # Increased to 20 for better coverage of all fields
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
            "Extract real values when found, use 'Failed to disclose' only when information is genuinely absent after careful search."
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
        # Normalize to schema headers
        llm_norm = coerce_to_headers(obj, headers, project_name=project) or {}
        seeds_norm = coerce_to_headers(seeds, headers, project_name=project) or {}
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

        # Evidence merge
        ev: List[Dict[str, Any]] = []
        if isinstance(seeds_norm.get("_evidence"), list):
            ev += seeds_norm["_evidence"]
        if isinstance(llm_norm.get("_evidence"), list):
            ev += llm_norm["_evidence"]
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
