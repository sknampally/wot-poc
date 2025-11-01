from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Set
import logging
import requests

from app import CACHE_DIR

log = logging.getLogger(__name__)


def _normalize_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    # Replace spaces and weird whitespace
    u = re.sub(r"\s+", "", u)
    # Make sure we have a scheme
    if not re.match(r"^https?://", u):
        u = "https://" + u.lstrip("/")
    # Drop trailing slash noise like double slashes
    u = re.sub(r"/{2,}", "/", u.replace("://", "§§")).replace("§§", "://")
    return u


def _serpapi_search(query: str, api_key: str, num: int = 25) -> List[str]:
    """Google results via SerpAPI (if SERPAPI_KEY is set)."""
    try:
        r = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "num": min(max(num, 1), 100),
                "api_key": api_key,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        urls: List[str] = []
        for item in data.get("organic_results", []) or []:
            link = _normalize_url(item.get("link") or "")
            if link:
                urls.append(link)
        log.info("[search] %s: SERPAPI results → %d", query, len(urls))
        return urls
    except Exception as e:
        log.warning("[search] SERPAPI failed: %s", e)
        return []


def _fallback_seeds(project: str) -> List[str]:
    """Deterministic seeds when SERPAPI is not available."""
    base = re.sub(r"[^\w\-\. ]+", " ", project).strip().lower()
    token = re.sub(r"\s+", "", base)
    domains = [
        f"{token}.io",
        f"{token}.com",
        f"{token}.org",
        f"{token}.net",
        f"{token}.id",
        f"{token}.ai",
    ]
    paths = ["", "/blog", "/docs", "/developers", "/solutions", "/products",
             "/about", "/news", "/ssi", "/digital-identity"]

    urls: List[str] = []
    for d in domains:
        for p in paths:
            urls.append(_normalize_url(f"https://{d}{p}"))

    # A few generic homes for projects that only live on platforms
    platform_homes = [
        f"https://github.com/{token}",
        f"https://www.linkedin.com/company/{token}/",
        f"https://medium.com/@{token}",
        f"https://{token}.substack.com/",
    ]
    urls.extend(platform_homes)
    return urls


def search_urls(project: str, target_count: int = 25) -> List[str]:
    """
    Returns a LIST OF STRINGS (URLs), deduped and normalized.
    Also writes `urls.json` in the project cache folder.
    """
    api_key = os.getenv("SERPAPI_KEY", "").strip()
    if api_key:
        log.info("[search] %s: using SERPAPI", project)
        urls = _serpapi_search(f"{project} digital identity", api_key, num=target_count)
        if not urls:
            # Fallback if SerpAPI returns nothing
            urls = _fallback_seeds(project)
    else:
        log.info("[search] %s: SERPAPI_KEY not set → using fallback seeds", project)
        urls = _fallback_seeds(project)
        log.info("[search] %s: fallback seeds → %d", project, len(urls))

    # Dedupe while preserving order
    seen: Set[str] = set()
    clean: List[str] = []
    for u in urls:
        nu = _normalize_url(u)
        if nu and nu not in seen:
            seen.add(nu)
            clean.append(nu)

    if target_count > 0:
        clean = clean[:target_count]

    log.info("[search] %s: done, total urls=%d", project, len(clean))

    # Persist to cache
    proj_dir = Path(CACHE_DIR) / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "urls.json").write_text(json.dumps(clean, indent=2), encoding="utf-8")
    log.info("[search] %s: wrote %d urls → %s", project, len(clean), proj_dir / "urls.json")

    return clean
