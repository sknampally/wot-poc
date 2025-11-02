"""
URL searcher using SerpAPI (Google search API) with fallback seed URLs.

This module finds relevant URLs for a project by:
1. Using SerpAPI to perform Google searches with human-like queries
2. Extracting URLs from multiple Google result types:
   - Organic results (main search results)
   - Sitelinks (expanded navigation)
   - Answer boxes (featured snippets)
   - Knowledge graph (Google info panel)
   - People also ask (PAA)
   - Related questions
   - Top stories/news
3. Filtering out irrelevant sources (YouTube, social media, etc.)
4. Prioritizing official sources (homepage, about pages, docs)
5. Using fallback seed URLs if SerpAPI unavailable

The known_website parameter helps target the correct entity when multiple
projects share similar names.
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

# ---------------------------
# Helper Functions
# ---------------------------

def _slugify_name(name: str) -> str:
    """
    Convert project name to URL-friendly slug.
    
    Example:
        "Trusted Biz" → "trusted-biz"
        "MÁS" → "mas"
    
    Args:
        name: Project name
    
    Returns:
        str: URL-friendly slug
    """
    s = "".join(ch if ch.isalnum() else " " for ch in (name or "")).strip().lower()
    return re.sub(r"\s+", "-", s)

def _maybe_domain_from_name(name: str) -> str | None:
    """
    Try to infer a domain name from project name (best-effort).
    
    Returns domain if name already contains a TLD-like suffix (e.g., "cheqd.io"),
    otherwise None. This helps with site-specific Google searches.
    
    Args:
        name: Project name
    
    Returns:
        str | None: Guessed domain (e.g., "cheqd.io") or None
    
    Example:
        "cheqd.io" → "cheqd.io"
        "cheqd" → None
    """
    cand = (name or "").strip().lower()
    cand = re.sub(r"[^a-z0-9\.\- ]+", "", cand)  # Keep only alphanumeric, dots, dashes
    cand = cand.replace(" ", "")
    
    # If looks like domain.tld (contains a dot and not starting/ending with dot)
    if "." in cand and not cand.startswith(".") and not cand.endswith("."):
        return cand
    return None

def _normalize_url(url: str) -> str | None:
    """
    Normalize and validate a URL.
    
    Handles:
    - Missing scheme (adds https://)
    - JavaScript URLs (rejects)
    - Invalid/malformed URLs
    
    Args:
        url: Raw URL string
    
    Returns:
        str | None: Normalized URL or None if invalid
    """
    try:
        url = (url or "").strip()
        if not url:
            return None
        
        # Reject JavaScript URLs (security)
        if url.lower().startswith("javascript:"):
            return None
        
        # Ensure scheme (https://)
        if not re.match(r"^https?://", url, flags=re.I):
            url = "https://" + url
        
        # Validate URL has a valid netloc (domain)
        parsed = urlparse(url)
        if not parsed.netloc:
            return None
        
        return url
    except Exception:
        return None

def _dedupe_keep_order(items: List[str]) -> List[str]:
    """
    Remove duplicates from list while preserving order.
    
    Args:
        items: List of items (may contain duplicates)
    
    Returns:
        List[str]: Deduplicated list in original order
    """
    seen = set()
    out = []
    for u in items:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out

def _cap(items: List[Any], limit: int) -> List[Any]:
    """
    Limit list to maximum length.
    
    Args:
        items: List to cap
        limit: Maximum number of items
    
    Returns:
        List[Any]: Capped list (minimum 1 item if original had items)
    """
    return items[: max(1, limit)]

# ---------------------------
# Seed URLs (Fallback)
# ---------------------------

def _generic_seeds(project: str, limit: int) -> List[str]:
    """
    Generate brand-agnostic seed URLs as fallback.
    
    Creates URLs by combining project name slug with common TLDs and paths.
    Used when SerpAPI is unavailable or returns few results.
    
    Args:
        project: Project name
        limit: Maximum number of seed URLs to generate
    
    Returns:
        List[str]: List of potential URLs (normalized)
    
    Example:
        Project "cheqd" → ["https://cheqd.io", "https://cheqd.com/about", ...]
    """
    # Convert to URL-friendly slug
    base = re.sub(r"[^a-z0-9\-]+", "", (project or "").strip().lower().replace(" ", "-"))
    if not base:
        return []

    # Common TLDs for tech companies
    tlds = ["io", "com", "org", "net", "ai", "id"]
    
    # Common paths that might contain useful information
    paths = [
        "",  # Homepage
        "/blog", "/docs", "/developers", "/solutions", "/products",
        "/about", "/news", "/ssi", "/identity", "/digital-identity"
    ]

    seeds: List[str] = []
    for tld in tlds:
        b = f"{base}.{tld}"
        for p in paths:
            full = _normalize_url(f"https://{b}{p}")
            if full:
                seeds.append(full)

    # Dedupe and limit
    return _cap(_dedupe_keep_order(seeds), limit)

# ---------------------------
# SerpAPI Integration
# ---------------------------

def _serpapi_search(queries: List[str], num: int, api_key: str) -> Tuple[List[str], Dict[str, Any]]:
    """
    Run multiple SerpAPI queries and extract URLs from comprehensive results.
    
    Extracts URLs from multiple Google result types (like a human browsing):
    - Organic results: Main search results (prioritizes official domains)
    - Sitelinks: Expanded navigation links from organic results
    - Answer boxes: Featured snippets / answer boxes
    - Knowledge graph: Google's info panel (official website)
    - People also ask (PAA): Related question URLs
    - Related questions: Additional related URLs
    - Top stories: News articles
    
    Filters out irrelevant sources:
    - YouTube videos, review sites, social media posts
    - Archive sites (web.archive.org)
    
    Args:
        queries: List of search queries to run
        num: Number of results per query (max 100)
        api_key: SerpAPI API key
    
    Returns:
        Tuple[List[str], Dict[str, Any]]: 
            - List of extracted URLs (deduplicated)
            - Debug blob with queries and response metadata
    
    Note:
        Adds 500ms delay between queries to avoid rate limiting (human-like behavior).
    """
    all_urls: List[str] = []
    debug: Dict[str, Any] = {"queries": [], "responses": []}

    endpoint = "https://serpapi.com/search"
    headers = {"User-Agent": "wot-poc/1.0 (+https://example.local)"}

    # Process each query
    for i, q in enumerate(queries):
        # Small delay between queries to avoid rate limiting (human-like behavior)
        # OPTIMIZED: Reduced delay since we have fewer queries now
        if i > 0:
            time.sleep(0.2)  # 200ms delay between queries (optimized for speed)
        
        # Build SerpAPI request
        params = {
            "engine": "google",
            "q": q,
            "api_key": api_key,
            "num": min(num, 100),  # API max - get more results per query
            "hl": "en",  # Language: English
            "gl": "us",  # Country: US
            "safe": "active",  # Safe search enabled
        }
        debug["queries"].append({"q": q, "params": params.copy()})

        try:
            # Make API request
            r = requests.get(endpoint, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            
            # Check for API errors in response
            if "error" in data:
                log.warning("[search] SerpAPI error in response for %r: %s", q, data.get("error"))
                continue
                
        except requests.exceptions.RequestException as e:
            log.warning("[search] SerpAPI request error for %r: %s", q, e)
            continue
        except Exception as e:
            log.warning("[search] SerpAPI unexpected error for %r: %s", q, e)
            continue

        # Store response metadata for debugging
        debug["responses"].append({"q": q, "status": r.status_code, "keys": list(data.keys())})

        # 1. Organic results (main search results) - prioritize official domains
        org = data.get("organic_results") or []
        for item in org:
            link = _normalize_url(item.get("link"))
            if link:
                # Filter out irrelevant sources
                link_lower = link.lower()
                # Skip YouTube videos, review sites, social media posts, archives
                if any(skip in link_lower for skip in [
                    'youtube.com/watch', 'youtube.com/channel',
                    '.reviews/', '.review/', 
                    'facebook.com/', 'twitter.com/', 'x.com/status',
                    'linkedin.com/posts/', 'reddit.com/',
                    'archive.org/', 'archive.ph/', 'web.archive.org'
                ]):
                    continue
                all_urls.append(link)

            # Extract sitelinks (expanded navigation links from search results)
            # These are often useful (About, Products, etc.)
            sl = item.get("sitelinks") or {}
            for variant in ("inline", "expanded"):
                for s in sl.get(variant, []) or []:
                    l2 = _normalize_url(s.get("link"))
                    if l2:
                        # Filter sitelinks too
                        l2_lower = l2.lower()
                        if any(skip in l2_lower for skip in [
                            'youtube.com/watch', '.reviews/', 'facebook.com/', 'twitter.com/'
                        ]):
                            continue
                        all_urls.append(l2)

        # 2. Answer box / featured snippet URLs
        # These are often high-quality sources
        answer_box = data.get("answer_box", {})
        if isinstance(answer_box, dict):
            answer_link = _normalize_url(answer_box.get("link"))
            if answer_link:
                all_urls.append(answer_link)

        # 3. Knowledge graph URLs (Google's info panel)
        # Usually contains official website
        kg = data.get("knowledge_graph", {})
        if isinstance(kg, dict):
            # Knowledge graph source URLs
            kg_url = _normalize_url(kg.get("website"))
            if kg_url:
                all_urls.append(kg_url)
            # Official website from knowledge graph
            official_site = _normalize_url(kg.get("official_website"))
            if official_site:
                all_urls.append(official_site)

        # 4. People also ask (PAA) URLs
        # Related questions often link to useful pages
        paa = data.get("people_also_ask", [])
        for paa_item in paa:
            if isinstance(paa_item, dict):
                paa_link = _normalize_url(paa_item.get("link"))
                if paa_link:
                    all_urls.append(paa_link)
                # Extract sitelinks from PAA too
                paa_sitelinks = paa_item.get("sitelinks", {})
                for variant in ("inline", "expanded"):
                    for sl in paa_sitelinks.get(variant, []) or []:
                        sl_link = _normalize_url(sl.get("link"))
                        if sl_link:
                            all_urls.append(sl_link)

        # 5. Related questions URLs
        related_questions = data.get("related_questions", [])
        for rq in related_questions:
            if isinstance(rq, dict):
                rq_link = _normalize_url(rq.get("link"))
                if rq_link:
                    all_urls.append(rq_link)

        # 6. Top stories / news (if relevant)
        # News articles may contain announcements, funding info, etc.
        top_stories = data.get("top_stories", [])
        for story in top_stories:
            if isinstance(story, dict):
                story_link = _normalize_url(story.get("link"))
                if story_link:
                    all_urls.append(story_link)

        log.info("[search] Query '%s' → extracted %d urls from SerpAPI", q, len(all_urls))

    # Deduplicate and return
    return _dedupe_keep_order(all_urls), debug

# ---------------------------
# Public API
# ---------------------------

def search_urls(project: str, cache_dir: Path | None = None, known_website: str = "") -> List[Dict[str, str]]:
    """
    Search for relevant URLs for a project using SerpAPI and fallback seeds.
    
    Strategy:
    1. If SerpAPI key available:
       - Run human-like Google searches (with digital identity context)
       - Extract URLs from multiple result types
       - Filter irrelevant sources
       - Prioritize known website if provided
    2. Merge with generic seed URLs (fallback)
    3. Deduplicate and sort by priority (official sources first)
    4. Cap to MAX_URLS_PER_PROJECT limit
    5. Cache results to cache_dir/urls.json
    
    Args:
        project: Project name (e.g., "cheqd", "Trusted Biz")
        cache_dir: Optional directory to cache URL list
        known_website: Optional known website URL (helps target correct entity)
    
    Returns:
        List[Dict[str, str]]: List of URL dicts, each with:
            - url: Normalized URL
            - source: "serpapi" or "seed"
    
    Note:
        URLs are sorted by priority:
        - Priority 0: Homepage (https://example.com)
        - Priority 1: About/company pages
        - Priority 2: GitHub repositories
        - Priority 3: Documentation pages
        - Priority 4: Blog/news
        - Priority 5: Everything else
    """
    max_urls = int(os.getenv("MAX_URLS_PER_PROJECT", "30"))  # Balanced default for speed and quality
    
    # Check both SERPAPI_KEY and SERPAPI_API_KEY for compatibility
    api_key = os.getenv("SERPAPI_API_KEY", os.getenv("SERPAPI_KEY", "")).strip()

    results: List[Dict[str, str]] = []

    # 1) SerpAPI search (if API key available)
    serp_urls: List[str] = []
    debug_blob: Dict[str, Any] = {}
    if api_key:
        # Use known website if provided, otherwise try to guess domain
        if known_website:
            # Extract domain from known website
            parsed = urlparse(known_website if known_website.startswith("http") else f"https://{known_website}")
            domain_guess = parsed.netloc.replace("www.", "")
            log.info("[search] %s: using known website domain: %s", project, domain_guess)
        else:
            # Try to infer domain from project name
            domain_guess = _maybe_domain_from_name(project)
        
        # Human-like Google search queries - prioritize digital identity context
        # OPTIMIZED: Reduced from 12-16 queries to 7-10 queries for faster processing
        # Include industry terms to avoid wrong entities (e.g., "Trusted Biz" vs "Trusted Business Solutions")
        # CRITICAL: When we have a known_website, prioritize it and add domain-specific searches
        if known_website:
            # Extract domain from known website for more specific searches
            parsed = urlparse(known_website if known_website.startswith("http") else f"https://{known_website}")
            known_domain = parsed.netloc.replace("www.", "")
            # Use site: queries to prioritize the known website domain
            queries = [
                # Highest priority: search within the known website domain
                f'site:{known_domain}',  # Site search for the known website
                f'site:{known_domain} about mission',  # About/mission pages from known site
                f'"{project}" site:{known_domain}',  # Project name within known domain
                # Then broader searches with industry context
                f'"{project}" digital identity SSI verifiable credentials',  # Combined: very specific + technical terms
                f'{project} official website about mission',  # Combined: website + about + mission
                f'{project} founded established launched announcement',  # Combined: dates info
                f'{project} funding investors partners',  # Combined: funding + partnerships
            ]
        else:
            queries = [
                # Most specific first - include industry context to avoid wrong entities
                f'"{project}" digital identity SSI verifiable credentials',  # Combined: very specific + technical terms
                f'{project} official website about mission',  # Combined: website + about + mission
                f'{project} founded established launched announcement',  # Combined: dates info
                f'{project} funding investors partners',  # Combined: funding + partnerships
                f'{project} github repository open source',  # Combined: code repository
            ]
        
        # Add domain-specific searches if we have a domain (and no known_website already added site: queries)
        # These help find content within the official site
        # Only add if we don't already have site: queries from known_website
        if domain_guess and not known_website:
            queries.extend([
                f'site:{domain_guess}',  # Site search - broad coverage
                f'site:{domain_guess} about mission company',  # Combined: about/mission/company pages
            ])
        
        # Get more results per query to mimic human browsing behavior
        # OPTIMIZED: Increase results per query since we have fewer queries now (faster overall)
        # Distribute max_urls across queries, with min 20 and max 40 per query
        results_per_query = max(20, min(max_urls // len(queries), 40))
        
        # Run SerpAPI searches
        serp_urls, debug_blob = _serpapi_search(queries, num=results_per_query, api_key=api_key)
        
        # If we have a known website, add it as highest priority URL
        # Ensures we always scrape the official site even if SerpAPI doesn't return it
        if known_website:
            known_clean = known_website.rstrip('/')
            # Normalize it
            known_normalized = _normalize_url(known_clean)
            if known_normalized and known_normalized not in serp_urls:
                serp_urls.insert(0, known_normalized)  # Insert at front for highest priority
                log.info("[search] %s: added known website as priority URL: %s", project, known_normalized)
        log.info("[search] %s: SerpAPI returned %d urls from %d queries", project, len(serp_urls), len(queries))

    # 2) Fallback seeds (always add a few; SerpAPI doesn't always catch product docs)
    # Useful for finding documentation, developer pages, etc.
    seed_urls = _generic_seeds(project, limit=max_urls)
    log.info("[search] %s: fallback seeds → %d", project, len(seed_urls))

    # 3) Merge with priority: SerpAPI URLs first (usually better quality), then seeds
    merged = _dedupe_keep_order(serp_urls + seed_urls)
    
    # 4) CRITICAL: If we have a known_website, prioritize URLs from that domain
    # This ensures we get the correct entity (e.g., Spanish MÁS vs Muslim American Society)
    if known_website:
        parsed_known = urlparse(known_website if known_website.startswith("http") else f"https://{known_website}")
        known_domain = parsed_known.netloc.lower().replace("www.", "")
        
        # Separate URLs: known domain vs others
        known_domain_urls = []
        other_urls = []
        
        for url in merged:
            try:
                parsed = urlparse(url)
                url_domain = parsed.netloc.lower().replace("www.", "")
                # Check if URL is from known domain
                if known_domain in url_domain or url_domain in known_domain:
                    known_domain_urls.append(url)
                else:
                    # Filter out clearly wrong domains if we have a known website
                    # This helps avoid wrong entities (e.g., Muslim American Society when looking for Spanish MÁS)
                    # Only filter if the domain is clearly a different entity
                    should_skip = False
                    # Example: If known_domain is masfan.rfef.es, skip muslimamericansociety.org, mas.org, etc.
                    # But this is contextual - be conservative and only skip obvious mismatches
                    if any(wrong in url_domain for wrong in ['muslimamericansociety', 'mas.org', 'masnewyork']):
                        if known_domain not in ['muslimamericansociety', 'mas.org']:
                            should_skip = True  # Skip wrong entity
                    
                    if not should_skip:
                        other_urls.append(url)
            except Exception:
                # Keep URL if parsing fails
                other_urls.append(url)
        
        # Prioritize: known domain URLs first, then others
        merged = known_domain_urls + other_urls
        log.info("[search] %s: Prioritized %d URLs from known domain '%s'", project, len(known_domain_urls), known_domain)
    
    merged = _cap(merged, max_urls)

    # 5) Sort by priority: official domains first, then others
    def _url_priority(url: str) -> int:
        """
        Calculate URL priority (lower number = higher priority).
        
        Priority order:
        0: Homepage (https://example.com or https://example.com/)
        1: About/company/mission pages
        2: GitHub repositories
        3: Documentation/developer pages
        4: Blog/news pages
        5: Everything else
        """
        url_lower = url.lower()
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        
        # Highest priority: official homepage (no path or just /)
        if url.count('/') <= 3:  # e.g., https://example.com or https://example.com/
            return 0
        
        # High priority: Official info pages
        if any(x in url_lower for x in ['/about', '/company', '/mission', '/who-we-are']):
            return 1
        
        # Medium-high: GitHub (usually official)
        if 'github.com' in domain:
            return 2
        
        # Medium: Docs/technical (still very useful)
        if any(x in url_lower for x in ['/docs', '/documentation', '/developers', '/developer']):
            return 3
        
        # Medium-low: Blog/news (may have company info)
        if any(x in url_lower for x in ['/blog', '/news', '/press', '/articles']):
            return 4
        
        # Lower priority: everything else
        return 5
    
    merged = sorted(merged, key=_url_priority)

    # 5) Wrap for downstream (add source metadata)
    results = [{"url": u, "source": ("serpapi" if u in serp_urls else "seed")} for u in merged]
    log.info("[search] %s: done, total urls=%d", project, len(results))

    # 6) Cache results for debugging and reproducibility
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_dir / "urls.json", "w") as f:
            json.dump(results, f, indent=2)
        # Optional debug dump for SerpAPI (shows queries and responses)
        if api_key and debug_blob:
            with open(cache_dir / "serpapi_debug.json", "w") as f:
                json.dump(debug_blob, f, indent=2)
        log.info("[search] %s: wrote %d urls → %s", project, len(results), cache_dir / "urls.json")

    return results
