"""
Web scraper for fetching and extracting text content from URLs.

This module handles:
- URL normalization (adding schemes, cleaning spaces)
- HTTP fetching with proper headers
- HTML parsing and text extraction (removes scripts, styles)
- Content caching for debugging

The scraper converts HTML to clean, readable text that can be used by the LLM.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

@dataclass
class Page:
    """
    Represents a scraped web page.
    
    Attributes:
        url: The normalized URL
        status: HTTP status code (None if request failed)
        mime: Content-Type header value
        text: Extracted readable text content
    """
    url: str
    status: int | None
    mime: str | None
    text: str

def _normalize_url(item: str | dict[str, Any]) -> str | None:
    """
    Normalize and clean a URL from various input formats.
    
    Handles:
    - String URLs: Direct URL strings
    - Dict URLs: {"url": "...", ...} format
    - Missing schemes: Adds https:// if missing
    - Embedded spaces: Removes spaces from URLs
    
    Args:
        item: Either a string URL or dict with 'url' key
    
    Returns:
        str: Normalized URL, or None if invalid/empty
    """
    # Extract URL from dict or string
    if isinstance(item, dict):
        u = (item.get("url") or "").strip()
    else:
        u = (item or "").strip()
    if not u:
        return None

    # Some inputs had embedded spaces (e.g., "trusted biz.io")
    # Remove all spaces from URLs
    u = u.replace(" ", "")

    # Ensure scheme (https://) if missing
    parts = urlsplit(u)
    if not parts.scheme:
        u = "https://" + u
        parts = urlsplit(u)

    # Rebuild URL to normalize (removes redundant parts, etc.)
    u = urlunsplit(parts)
    return u

def _extract_text(html: str) -> str:
    """
    Extract clean, readable text from HTML using BeautifulSoup.
    
    Removes:
    - <script> tags (JavaScript)
    - <style> tags (CSS)
    - <noscript> tags
    
    Then extracts all text content and normalizes whitespace.
    
    Args:
        html: Raw HTML string
    
    Returns:
        str: Clean text content with normalized whitespace
    """
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style elements (they're not useful for text extraction)
        for script in soup(["script", "style", "noscript"]):
            script.decompose()
        
        # Get text content, separated by spaces
        text = soup.get_text(separator=' ', strip=True)
        
        # Collapse multiple spaces/newlines into single space
        import re
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    except Exception as e:
        log.warning("[scrape] text extraction failed: %s", e)
        return ""

def _fetch(url: str, timeout=10) -> Page:  # Reduced timeout from 15s to 10s for faster processing
    """
    Fetch a single URL and extract its text content.
    
    Args:
        url: The URL to fetch
        timeout: Request timeout in seconds (default: 15)
    
    Returns:
        Page: Page object with url, status, mime, and extracted text
    
    Note:
        Uses a user-agent string to appear as a regular browser.
        Only processes text/html and JSON content types.
    """
    try:
        # Use a browser-like user agent to avoid blocking
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; wot-poc/1.0; +https://example.local)"
        }
        
        # Make HTTP GET request
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        
        # Check content type
        ctype = r.headers.get("content-type", "")
        text = r.text if ("text/" in ctype or "json" in ctype or ctype == "") else ""
        
        # Extract readable text from HTML if it's HTML content
        if text and ("html" in ctype.lower() or not ctype):
            extracted_text = _extract_text(text)
            if extracted_text:
                text = extracted_text
                log.info("[scrape] extracted readable text: %d chars from HTML", len(text))
        
        # Log result
        if r.status_code != 200:
            log.info("[scrape] non-200 %s → %s :: non-200 %s", r.status_code, url, r.status_code)
        else:
            log.info("[scrape] %s → %s → %d chars :: %s", ctype.split(";")[0] or "text/html", url, len(text), url)
        
        return Page(url=url, status=r.status_code, mime=ctype, text=text)
    except requests.RequestException as e:
        log.warning("[scrape] ERROR :: %s :: %s", url, e)
        return Page(url=url, status=None, mime=None, text="")

def scrape_urls(project: str, url_items: list, cache_dir: Path | None = None) -> list[dict]:
    """
    Scrape multiple URLs for a project and extract text content.
    
    For each URL:
    1. Normalizes the URL (adds scheme, removes spaces)
    2. Fetches the page
    3. Extracts clean text from HTML
    4. Optionally caches the result for debugging
    
    Args:
        project: Project name (for logging and cache organization)
        url_items: List of URLs (strings or dicts with 'url' key)
        cache_dir: Optional directory to cache scraped text files
    
    Returns:
        list[dict]: List of page dicts, each with:
            - url: The normalized URL
            - status: HTTP status code
            - mime: Content-Type
            - text: Extracted text content
    
    Note:
        Cached files are saved as cache_dir/texts/01.txt, 02.txt, etc.
        Each cache file contains the URL and first 100KB of text.
    """
    log.info("[scrape] start for %s: %d urls", project, len(url_items))
    pages: list[dict] = []
    
    # Process each URL
    for item in url_items:
        url = _normalize_url(item)
        if not url:
            continue
        
        # Fetch and extract text
        page = _fetch(url)
        
        # Convert Page dataclass to dict for compatibility with extractor
        pages.append({
            "url": page.url,
            "status": page.status,
            "mime": page.mime,
            "text": page.text,
        })

    # Cache scraped text for debugging (optional)
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        texts_dir = cache_dir / "texts"
        texts_dir.mkdir(exist_ok=True)
        
        # Write each page to a numbered text file
        for i, p in enumerate(pages, start=1):
            # Keep small text cache to avoid huge files (first 100KB only)
            snippet = p.get("text", "")[:100000] if p.get("text") else ""
            url = p.get("url", "")
            
            # Format: [URL]url\ncontent
            with open(texts_dir / f"{i:02d}.txt", "w") as f:
                f.write(f"[URL]{url}\n{snippet}")
        
        log.info("[scrape] done: wrote texts to %s", texts_dir)
    
    return pages
