import os, json, time, re
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import lxml.html
from readability import Document
from lxml_html_clean import Cleaner
import trafilatura

# ---------- Tunables via env ----------
UA = os.getenv(
    "SCRAPE_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
CONNECT_TIMEOUT = float(os.getenv("SCRAPE_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("SCRAPE_READ_TIMEOUT", "25"))
MAX_RETRIES = int(os.getenv("SCRAPE_MAX_RETRIES", "3"))
BACKOFF = float(os.getenv("SCRAPE_BACKOFF", "0.5"))
MAX_BYTES = int(os.getenv("SCRAPE_MAX_BYTES", str(2_000_000)))  # 2 MB cap
SLEEP_BETWEEN = float(os.getenv("SCRAPE_SLEEP_BETWEEN", "0.4"))

# -------------------------------------


def _session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"])
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=8, pool_maxsize=8)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"})
    return s


def fetch(url: str) -> Tuple[Optional[bytes], Optional[str], Optional[int]]:
    s = _session()
    try:
        r = s.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
        status = r.status_code
        ctype = r.headers.get("Content-Type", "")
        if r.content and len(r.content) > MAX_BYTES:
            # Trim huge responses to keep memory/cpu sane
            content = r.content[:MAX_BYTES]
        else:
            content = r.content
        return content, ctype, status
    except Exception:
        return None, None, None


_cleaner = Cleaner(
    style=True, scripts=True, javascript=True, comments=True,
    annoying_tags=True, frames=True, forms=True, embedded=True,
    links=False, meta=False, page_structure=False, processing_instructions=True
)


def _extract_trafilatura(html_bytes: bytes, url: str) -> str:
    try:
        # Trafilatura prefers str; pass URL for better heuristics
        html = html_bytes.decode("utf-8", errors="ignore")
        text = trafilatura.extract(html, url=url, include_tables=True, favor_recall=True)
        return text or ""
    except Exception:
        return ""


def _extract_readability(html_bytes: bytes) -> str:
    try:
        html = html_bytes.decode("utf-8", errors="ignore")
        doc = Document(html)
        summary = doc.summary(html_partial=True)  # cleaned HTML fragment
        root = lxml.html.fromstring(summary)
        _cleaner(root)
        text = root.text_content()
        return re.sub(r"\s+\n", "\n", text).strip()
    except Exception:
        return ""


def clean_html(html_bytes: Optional[bytes], url: str, content_type: str) -> str:
    if not html_bytes:
        return ""
    # Skip obvious non-HTML (e.g., PDFs) for now
    if "pdf" in (content_type or "").lower():
        return ""  # (Optional: add a PDF extractor later)
    # 1) try trafilatura
    text = _extract_trafilatura(html_bytes, url)
    if text and len(text) > 300:
        return text
    # 2) fallback readability
    text2 = _extract_readability(html_bytes)
    return text2 or text or ""


def scrape_urls(urls: List[Dict[str, Any]], out_dir: Path) -> List[Dict[str, Any]]:
    print(f"[scrape] start for {out_dir.name}: {len(urls)} urls")
    out = []
    tdir = out_dir / "texts"
    tdir.mkdir(parents=True, exist_ok=True)

    for i, item in enumerate(urls, 1):
        url = item.get("url", "")
        f = tdir / f"{i:02d}.json"
        if f.exists():
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
                out.append(meta)
                print(f"[scrape] {i}/{len(urls)} cache ✓ {url} "
                      f"({len(meta.get('text',''))} chars)")
            except Exception as e:
                print(f"[scrape] {i}/{len(urls)} cache read error: {e}")
            continue

        try:
            html_bytes, ctype, status = fetch(url)
            if status is None:
                print(f"[scrape] {i}/{len(urls)} fetch ✗ {url} (no response)")
                meta = {"url": url, "title": item.get("title", ""), "status": None,
                        "content_type": None, "text": "", "error": "fetch-failed"}
                f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                out.append(meta); time.sleep(SLEEP_BETWEEN); continue

            text = clean_html(html_bytes, url, ctype or "")
            meta = {
                "url": url,
                "title": item.get("title", ""),
                "status": status,
                "content_type": ctype,
                "text": text[:100000],  # cap for safety
            }
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append(meta)
            print(f"[scrape] {i}/{len(urls)} {status} {ctype or ''} "
                  f"→ {len(text)} chars :: {url}")
            time.sleep(SLEEP_BETWEEN)

        except Exception as e:
            print(f"[scrape] {i}/{len(urls)} error {url}: {e}")
            meta = {"url": url, "title": item.get("title", ""), "status": None,
                    "content_type": None, "text": "", "error": str(e)}
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append(meta)

    print(f"[scrape] done: wrote texts to {tdir}")
    return out
