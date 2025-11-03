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
from app.config.codebook import load_codebook, Codebook, load_prompts
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
    
    Uses codebook field definitions and JSON prompts to generate detailed instructions.
    This helps the LLM understand exactly what to extract and where to find it.
    
    Args:
        project: Project name to extract data for
        headers: List of field names to extract
        context: Packed text content from scraped pages
        codebook: Codebook with field definitions and extraction guidance
    
    Returns:
        str: Complete prompt string for LLM
    """
    # Load prompts from JSON file for easy tweaking by prompt engineers
    prompts = load_prompts()
    field_hints = prompts.get("field_hints", {})
    type_hints = prompts.get("type_hints", {})
    
    # Build field-specific extraction hints from codebook definitions and JSON prompts
    field_guidance_lines = []
    for h in headers:
        # Skip Product Name, ID, and Logo - these are input fields or URLs, not extracted content
        # Product Name: Input value (not extracted)
        # ID: Internal identifier (not extracted)
        # Logo: URL to image file (not text to extract)
        if h.strip() in ["Product Name", "ID", "Logo"]:
            continue
        # First check if we have a field definition in codebook
        field_def = codebook.get_field(h)
        if field_def:
            data_definition = field_def.get("data_definition", "")
            response_type = field_def.get("response_type", "")
            
            hint_parts = [f"- {h}:"]
            # Use data_definition as primary instruction - this tells exactly what to extract
            if data_definition and data_definition.strip():
                # Clean up newlines and make it readable
                guidance = data_definition.strip().replace('\n', ' ')
                hint_parts.append(f"{guidance}")
            if response_type and response_type.strip():
                hint_parts.append(f"Type: {response_type}")
            
            # Add specific hints from JSON prompts based on field name patterns
            h_lower = h.lower()
            if "mission" in h_lower and "mission" in field_hints:
                hint_parts.append(field_hints["mission"])
            if "funding" in h_lower and "funding" in field_hints:
                hint_parts.append(field_hints["funding"])
            if ("partner" in h_lower or "affiliated" in h_lower) and "partner_affiliated" in field_hints:
                hint_parts.append(field_hints["partner_affiliated"])
            if "repository" in h_lower and "code" in h_lower and "repository_code" in field_hints:
                hint_parts.append(field_hints["repository_code"])
            if ("politically" in h_lower or "government" in h_lower) and "politically_government" in field_hints:
                hint_parts.append(field_hints["politically_government"])
            if ("managing entity" in h_lower or ("managing" in h_lower and "entity" in h_lower)) and "managing_entity" in field_hints:
                hint_parts.append(field_hints["managing_entity"])
            if ("app store" in h_lower or ("app" in h_lower and "store" in h_lower)) and "app_store" in field_hints:
                hint_parts.append(field_hints["app_store"])
            if "exportable" in h_lower and "credential" in h_lower and "exportable_credentials" in field_hints:
                hint_parts.append(field_hints["exportable_credentials"])
            if "credential" in h_lower and "key storage" in h_lower and "credential_key_storage" in field_hints:
                hint_parts.append(field_hints["credential_key_storage"])
            if ("zkp" in h_lower or "zero-knowledge" in h_lower) and "zkp_zero_knowledge" in field_hints:
                hint_parts.append(field_hints["zkp_zero_knowledge"])
            if "targets holders" in h_lower and "targets_holders" in field_hints:
                hint_parts.append(field_hints["targets_holders"])
            if "targets issuers" in h_lower and "targets_issuers" in field_hints:
                hint_parts.append(field_hints["targets_issuers"])
            if "targets verifiers" in h_lower and "targets_verifiers" in field_hints:
                hint_parts.append(field_hints["targets_verifiers"])
            if "blockchain" in h_lower and ("registr" in h_lower or "data" in h_lower) and "blockchain_registry" in field_hints:
                hint_parts.append(field_hints["blockchain_registry"])
            if ("tech stack" in h_lower or "technology" in h_lower) and "tech_stack" in field_hints:
                hint_parts.append(field_hints["tech_stack"])
            
            # Determine if this field allows "Failed to disclose"
            # Only specific fields explicitly allow it per data definitions  
            allows_failed_to_disclose = False
            data_definition_lower = data_definition.lower() if data_definition else ""
            if "failed to disclose" in data_definition_lower:
                allows_failed_to_disclose = True
            
            # Add type-specific instructions based on response_type
            response_type_lower = response_type.lower() if response_type else ""
            if "boolean" in response_type_lower or "ternary" in response_type_lower or response_type.startswith("[Options]") and "Failed to disclose" in response_type:
                # Boolean/ternary/options fields can use ternary_enums (which includes "Failed to disclose")
                hint_parts.append(f"Must be one of: {codebook.ternary_enums}")
            elif "Options" in response_type and "Announced" in response_type:
                # Status field uses status_enums
                hint_parts.append(f"Must be one of: {codebook.status_enums}")
            elif "[url]" in response_type_lower or "[image]" in response_type_lower:
                # URL/image fields: use empty string if not found, NEVER "Failed to disclose"
                if not allows_failed_to_disclose and "url_image_empty" in type_hints:
                    hint_parts.append(type_hints["url_image_empty"])
            elif "[year]" in response_type_lower:
                if "announcement" in h.lower() and "year_announcement" in type_hints:
                    hint_parts.append(type_hints["year_announcement"])
                elif "launch" in h.lower() and "year_launch" in type_hints:
                    hint_parts.append(type_hints["year_launch"])
                elif "year_generic" in type_hints:
                    hint_parts.append(type_hints["year_generic"])
                # Year fields: use empty string if not found, NEVER "Failed to disclose"
                if not allows_failed_to_disclose and "year_empty" in type_hints:
                    hint_parts.append(type_hints["year_empty"])
            elif "[text]" in response_type_lower or "[entity name]" in response_type_lower or "[person]" in response_type_lower:
                # Text fields: only allow "Failed to disclose" if explicitly stated in data_definition
                if not allows_failed_to_disclose and "text_empty" in type_hints:
                    hint_parts.append(type_hints["text_empty"])
            
            # Add valid values if present in response_type
            if response_type.startswith("[Options]") and "{" in response_type:
                # Parse options from response_type like "[Options] {True, False, Failed to disclose}"
                options_part = response_type.split("{")[1].split("}")[0]
                options = [opt.strip() for opt in options_part.split(",")]
                hint_parts.append(f"Valid values: {', '.join(options)}")
            
            if len(hint_parts) > 1:  # More than just the field name
                field_guidance_lines.append(" ".join(hint_parts))
            continue
    
    # If no codebook definitions, use fallback guidance from JSON
    if not field_guidance_lines:
        fallback_hints = prompts.get("fallback_hints", [])
        field_guidance_lines = [f"- {hint}" for hint in fallback_hints]
    
    field_guidance = "\n".join(field_guidance_lines)
    
    # Use user prompt template from JSON (with fallback if missing)
    user_prompt_template = prompts.get("user_prompt_template", "You are extracting structured data about a digital identity project.\n\nCRITICAL RULES:\n1. Set 'Product Name' exactly to: `{project_name}`\n{field_guidance}\n")
    
    # Format the prompt template with dynamic values
    instructions = user_prompt_template \
        .replace("{project_name}", project) \
        .replace("{status_enums}", str(codebook.status_enums)) \
        .replace("{ternary}", str(codebook.ternary_enums)) \
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
    
    # Load excluded domains from prompts.json config
    prompts_config = load_prompts()
    excluded_domains = prompts_config.get("excluded_domains", [
        'youtube.com', 'facebook.com', 'twitter.com', 'x.com',
        '.reviews', 'linkedin.com', 'reddit.com', 'archive.', 'podcasts.apple.com'
    ])
    
    candidates = []
    for p in pages[:15]:  # Check first 15 pages
        url = (p.get("url") or "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        path = parsed.path.lower()
        
        # Skip non-official sources
        if any(skip in domain for skip in excluded_domains):
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
            if not any(skip in parsed.netloc.lower() for skip in excluded_domains):
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
    
    # Load exclude keywords from prompts.json config
    prompts_config = load_prompts()
    exclude_keywords = prompts_config.get("exclude_keywords", [
        'career', 'jobs', 'hiring', 'recruit', 'position', 'vacancy', 'apply now',
        'download', '.pdf', '.zip', '.exe', '.dmg',
        'cookie policy', 'privacy policy', 'terms of service', 'legal notice',
        'sitemap', 'robots.txt'
    ])
    
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
        
        # Very high priority: GitHub repositories (critical for technical fields)
        # GitHub READMEs and docs contain technical implementation details
        elif 'github.com' in u_lower:
            priority = 0.5
        
        # Very high priority: About/Company/Mission pages (mission statements usually here)
        elif any(x in u_lower for x in ['/about', '/company', '/mission', '/who-we-are', '/what-we-do', '/our-story']):
            priority = 0.7
        
        # High priority: FAQ pages (often contain mission statements and company info)
        elif '/faq' in u_lower:
            priority = 1
        # High priority: Documentation (for tech fields)
        elif any(x in u_lower for x in ['/docs', '/documentation', '/developers', '/developer']):
            priority = 1.5
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
    
    # Load system prompt from JSON (with fallback)
    prompts_config = load_prompts()
    system_prompt = prompts_config.get("system_prompt", (
        "You are a precise data extraction assistant specializing in digital identity and SSI projects. "
        "You extract structured information from web sources. "
        "You must ONLY use information explicitly found in the provided context. "
        "Be thorough - search all provided URLs and text. "
        "CRITICAL: Only 6-7 specific fields allow 'Failed to disclose' as a value. "
        "For all other fields, use empty string (\"\") if information is not found. "
        "Follow the field-specific value constraints in the user prompt exactly."
    ))

    messages = [
        {"role": "system", "content": system_prompt},
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
        # Load allowed fields from prompts.json config
        prompts_config = load_prompts()
        allowed_failed_to_disclose_fields_list = prompts_config.get("allowed_failed_to_disclose_fields", [
            "Uses/endorses ZKP",
            "Has Exportable Credentials",
            "Credential And Key Storage",
            "Targets Holders",
            "Targets Issuers",
            "Targets Verifiers",
        ])
        allowed_failed_to_disclose_fields = set(allowed_failed_to_disclose_fields_list)
        
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
