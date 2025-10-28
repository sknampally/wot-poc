# src/scraper.py
import os, json, time
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import requests
from lxml import html as lxml_html
from readability import Document
import trafilatura

UA = os.getenv("SCRAPER_UA", "Mozilla/5.0 (compatible; wot-poc-scraper/0.1; +https://example.org/)")
REQ_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "12"))
REQ_RETRIES = int(os.getenv("SCRAPER_RETRIES", "3"))
SLEEP_BETWEEN = float(os.getenv("SCRAPER_SLEEP", "0.35"))

def fetch(url: str) -> Tuple[int, str, bytes]:
    """
    Returns (status_code, content_type, content_bytes) or raises after retries.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, REQ_RETRIES + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=REQ_TIMEOUT, allow_redirects=True)
            ct = r.headers.get("Content-Type", "") or ""
            return r.status_code, ct, r.content
        except Exception as e:
            last_exc = e
            time.sleep(0.6 * attempt)
    raise RuntimeError(f"fetch failed after {REQ_RETRIES} attempts: {last_exc}")

def _clean_text(text: str) -> str:
    # Collapse whitespace a bit
    return " ".join(text.split())

def clean_html(url: str, status: int, content_type: str, content: bytes) -> str:
    """
    Extract readable text from HTML bytes. Skip PDFs and non-HTML.
    """
    ctype = (content_type or "").lower()
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        return ""

    # try trafilatura first
    try:
        extracted = trafilatura.extract(
            content,
            include_comments=False,
            include_formatting=False,
            include_tables=False,
            favor_recall=True,
            url=url
        )
        if extracted and extracted.strip():
            return _clean_text(extracted.strip())
    except Exception:
        pass

    # fallback: readability-lxml
    try:
        doc = Document(content)
        summary_html = doc.summary(html_partial=True)
        tree = lxml_html.fromstring(summary_html)
        txt = tree.text_content() or ""
        return _clean_text(txt)
    except Exception:
        pass

    # last resort: raw text scrape
    try:
        tree = lxml_html.fromstring(content)
        txt = tree.text_content() or ""
        return _clean_text(txt)
    except Exception:
        return ""

def scrape_urls(urls: List[Dict[str, str]], out_dir: Path) -> List[Dict[str, str]]:
    """
    Saves cleaned text to data/cache/<Project>/texts/i.json
    Returns a list of {url,title,text}
    """
    print(f"[scrape] start for {out_dir.name}: {len(urls)} urls")
    out: List[Dict[str, str]] = []
    tdir = out_dir / "texts"
    tdir.mkdir(parents=True, exist_ok=True)

    for i, item in enumerate(urls, 1):
        url = item.get("url", "").strip()
        title = item.get("title", "")
        f = tdir / f"{i:02d}.json"

        # cache
        if f.exists():
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                text = j.get("text", "")
                print(f"[scrape] {i}/{len(urls)} cache ✓ {url} ({len(text)} chars)")
                out.append(j)
                continue
            except Exception:
                pass

        # skip PDFs up-front
        if url.lower().split("?")[0].endswith(".pdf"):
            print(f"[scrape] {i}/{len(urls)} skip PDF :: {url}")
            meta = {"url": url, "title": title, "text": "", "error": "pdf_skipped"}
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append(meta)
            continue

        try:
            status, ct, body = fetch(url)
            if status != 200:
                meta = {"url": url, "title": title, "text": "", "error": f"status_{status}"}
                print(f"[scrape] {i}/{len(urls)} {status} {ct} → 0 chars :: {url}")
                f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                out.append(meta)
                continue

            text = clean_html(url, status, ct, body)
            meta = {"url": url, "title": title, "text": text}
            print(f"[scrape] {i}/{len(urls)} {status} {ct.split(';')[0]} → {len(text)} chars :: {url}")
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append(meta)
            time.sleep(SLEEP_BETWEEN)
        except Exception as e:
            meta = {"url": url, "title": title, "text": "", "error": str(e)}
            print(f"[scrape] {i}/{len(urls)} ERROR :: {url} :: {e}")
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append(meta)

    print(f"[scrape] done: wrote texts to {tdir}")
    return out
