# src/scraper.py
import os, json, time
from pathlib import Path
from typing import List, Dict
import requests
from dotenv import load_dotenv

# optional text extraction libs
try:
    import trafilatura  # best effort text extraction
except Exception:
    trafilatura = None

try:
    from readability import Document  # readability-lxml
except Exception:
    Document = None

load_dotenv()

# ---------- Scrape config (override via .env) ----------
CONNECT_TIMEOUT = float(os.getenv("SCRAPE_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT    = float(os.getenv("SCRAPE_READ_TIMEOUT", "25"))
MAX_RETRIES     = int(os.getenv("SCRAPE_MAX_RETRIES", "3"))
BACKOFF         = float(os.getenv("SCRAPE_BACKOFF", "0.5"))
MAX_BYTES       = int(os.getenv("SCRAPE_MAX_BYTES", "2000000"))  # 2MB cap
SLEEP_BETWEEN   = float(os.getenv("SCRAPE_SLEEP_BETWEEN", "0.4"))
UA = os.getenv(
    "SCRAPE_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


def _extract_text(url: str, html: str, content_type: str) -> str:
    """Return cleaned text from HTML; skip obvious binaries."""
    ctype = (content_type or "").lower()
    if "pdf" in ctype or "octet-stream" in ctype:
        # Let’s not attempt to parse PDFs in this POC
        return ""

    # 1) trafilatura if available
    if trafilatura is not None:
        try:
            txt = trafilatura.extract(html, include_comments=False, include_tables=False)
            if txt:
                return txt.strip()
        except Exception:
            pass

    # 2) readability as fallback
    if Document is not None:
        try:
            doc = Document(html)
            summary_html = doc.summary()
            # super simple tag-strip
            try:
                from bs4 import BeautifulSoup
                cleaned = BeautifulSoup(summary_html, "lxml").get_text(" ", strip=True)
                return cleaned
            except Exception:
                return summary_html
        except Exception:
            pass

    # 3) last resort: raw text (very noisy)
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    except Exception:
        return ""


def fetch(url: str) -> Dict[str, str]:
    """Fetch URL with retries, backoff, and size guard; return dict with html/text and meta."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=True,
                stream=True,
            )
            status = r.status_code
            ctype  = r.headers.get("Content-Type", "")
            # read with byte cap
            content = b""
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    content += chunk
                    if len(content) > MAX_BYTES:
                        break
            r.close()

            html = content.decode(errors="ignore")
            text = _extract_text(url, html, ctype)
            return {
                "status": status,
                "content_type": ctype,
                "html": html,
                "text": text,
            }
        except Exception as e:
            last_err = e
            time.sleep(BACKOFF * attempt)
            continue
    raise RuntimeError(f"fetch failed after {MAX_RETRIES} attempts: {last_err}")


def scrape_urls(urls: List[Dict[str, str]], out_dir: Path) -> List[Dict[str, str]]:
    """
    Given a list of {'url','title'}, fetch and clean each page.
    Caches to data/cache/<Project>/texts/NN.json
    Returns a list of {'url','title','text'} (and logs).
    """
    name = out_dir.name
    print(f"[scrape] start for {name}: {len(urls)} urls")
    out: List[Dict[str, str]] = []
    tdir = out_dir / "texts"
    tdir.mkdir(parents=True, exist_ok=True)

    for i, item in enumerate(urls, 1):
        url = (item.get("url") or "").strip()
        title = item.get("title", "")
        f = tdir / f"{i:02d}.json"

        # cache
        if f.exists():
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
                txt = meta.get("text", "") or ""
                print(f"[scrape] {i}/{len(urls)} cache ✓ {url} ({len(txt)} chars)")
                out.append(meta)
                continue
            except Exception:
                # fall through to refetch
                pass

        if not url:
            out.append({"url": url, "title": title, "text": "", "error": "empty url"})
            continue

        try:
            data = fetch(url)
            status = data.get("status", 0)
            ctype  = data.get("content_type", "")
            text   = (data.get("text") or "")[:100000]  # trim to 100k to keep things light
            print(f"[scrape] {i}/{len(urls)} {status} {ctype.split(';')[0]} → {len(text)} chars :: {url}")

            meta = {"url": url, "title": title, "text": text}
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append(meta)
            time.sleep(SLEEP_BETWEEN)
        except Exception as e:
            print(f"[scrape] {i}/{len(urls)} ERROR :: {url} :: {e}")
            meta = {"url": url, "title": title, "text": "", "error": str(e)}
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            out.append(meta)

    print(f"[scrape] done: wrote texts to {tdir}")
    return out
