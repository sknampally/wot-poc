# src/app/workers/scraper.py
from __future__ import annotations
import logging, time, json
from typing import List, Dict
from pathlib import Path
import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

def _fetch(url: str, timeout: float = 12.0) -> Dict[str, str]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200:
            log.info("[scrape] non-200 %s → %s", r.status_code, url)
            return {"url": url, "text": "", "status": str(r.status_code), "ctype": ctype}
        if "text/html" not in ctype:
            return {"url": url, "text": "", "status": "200", "ctype": ctype}
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "noscript"]): tag.extract()
        text = " ".join(soup.get_text(" ").split())
        return {"url": url, "text": text, "status": "200", "ctype": ctype}
    except Exception as e:
        log.warning("[scrape] ERROR :: %s :: %s", url, e)
        return {"url": url, "text": "", "status": "ERR", "ctype": ""}

def scrape_urls(project: str, url_items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    log.info("[scrape] start for %s: %d urls", project, len(url_items))

    root = Path(__file__).resolve().parents[3]
    outdir = root / "data" / "cache" / project / "texts"
    outdir.mkdir(parents=True, exist_ok=True)

    for idx, item in enumerate(url_items, 1):
        url = item["url"]
        rec = _fetch(url)
        tlen = len(rec.get("text", ""))
        src = item.get("source", "-")
        if rec["status"] == "200" and tlen:
            log.info("[scrape] %d/%d %s → %d chars :: %s", idx, len(url_items), rec.get("ctype",""), tlen, url)
        elif rec["status"] == "200":
            log.info("[scrape] %d/%d %s (no text) :: %s", idx, len(url_items), rec.get("ctype",""), url)
        elif rec["status"] == "ERR":
            # already logged at WARNING in _fetch
            pass
        else:
            log.info("[scrape] %d/%d (empty/blocked) :: %s", idx, len(url_items), url)

        # persist individual page text for traceability
        safe = (url.replace("://","_").replace("/","_")[:160]) or f"p{idx}"
        (outdir / f"{safe}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        out.append(rec)

    log.info("[scrape] done: wrote texts to %s", (root / "data" / "cache" / project / "texts"))
    return out
