from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
import requests

from app import CACHE_DIR

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)


def _coerce_url(item: Any) -> str:
    """Accept str | dict | tuple and return a normalized URL string or ''."""
    if isinstance(item, str):
        u = item
    elif isinstance(item, dict):
        u = item.get("url") or item.get("link") or ""
    elif isinstance(item, (list, tuple)) and item:
        u = item[0]
    else:
        u = ""
    u = (u or "").strip()
    u = re.sub(r"\s+", "", u)
    if not u:
        return ""
    if not re.match(r"^https?://", u):
        u = "https://" + u.lstrip("/")
    u = re.sub(r"/{2,}", "/", u.replace("://", "§§")).replace("§§", "://")
    return u


def _fetch(url: str, timeout: int = 15) -> Dict[str, Any]:
    """Fetch a single URL, returning a page record with text (or empty on error)."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
            allow_redirects=True,
        )
        status = r.status_code
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        txt = ""
        if 200 <= status < 300 and ctype in ("text/html", "text/plain"):
            r.encoding = r.apparent_encoding or r.encoding or "utf-8"
            txt = r.text or ""
            log.info("[scrape] %s → %s → %d chars :: %s", url, ctype or "text/html", len(txt), url)
        else:
            msg = f"non-200 {status}" if status != 200 else f"non-text {ctype or 'unknown'}"
            log.info("[scrape] %s → %s :: %s", msg, url, msg)
        return {"url": url, "status": status, "mime": ctype or "text/html", "text": txt}
    except requests.RequestException as e:
        log.warning("[scrape] ERROR :: %s :: %s", url, e)
        return {"url": url, "status": 0, "mime": "error/requests", "text": ""}


def scrape_urls(project: str, url_items: List[Any], save_texts: bool = True) -> List[Dict[str, Any]]:
    """Fetch a list of URLs (strings or dicts). Returns page records, writes cache."""
    proj_dir = Path(CACHE_DIR) / project
    texts_dir = proj_dir / "texts"
    proj_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    log.info("[scrape] start for %s: %d urls", project, len(url_items))

    pages: List[Dict[str, Any]] = []
    idx = 0
    for raw in url_items:
        url = _coerce_url(raw)
        if not url:
            continue
        rec = _fetch(url)
        idx += 1
        if rec.get("text"):
            # Write an easy-to-inspect text file per page (optional)
            if save_texts:
                safe = re.sub(r"[^a-zA-Z0-9\-\.]+", "_", re.sub(r"^https?://", "", url))[:80]
                (texts_dir / f"{idx:02d}-{safe}.txt").write_text(rec["text"], encoding="utf-8", errors="ignore")
            log.info("[scrape] %d/%d %s :: %s", idx, len(url_items), rec.get("mime", "text/html"), url)
        else:
            # already logged as non-200/blocked/error
            log.info("[scrape] %d/%d (empty/blocked) :: %s", idx, len(url_items), url)
        pages.append(rec)

    # Also keep a structured list without the full page text for quick glance
    lite = [{"url": p["url"], "status": p["status"], "mime": p["mime"], "chars": len(p.get("text") or "")} for p in pages]
    (proj_dir / "pages.json").write_text(__import__("json").dumps(lite, indent=2), encoding="utf-8")

    log.info("[scrape] done: wrote texts to %s", texts_dir)
    return pages
