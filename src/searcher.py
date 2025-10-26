import os, json, time, unicodedata
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse, urlsplit, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

# ---------- Env & defaults ----------
REGION = os.getenv("SEARCH_REGION", "us-en")   # for logs
SAFESEARCH = os.getenv("SEARCH_SAFESEARCH", "moderate")
BACKOFF = float(os.getenv("SEARCH_BACKOFF_SECONDS", "2"))
MAX_URLS = int(os.getenv("MAX_URLS_PER_PROJECT", "6"))

UA = os.getenv(
    "SEARCH_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

DISALLOW = ("facebook.com","instagram.com","tiktok.com","pinterest.com")
# ------------------------------------


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _clean_ddg_url(u: str) -> str:
    # DDG sometimes wraps as /l/?kh=-1&uddg=<encoded>
    if "uddg=" in u:
        try:
            q = parse_qs(urlsplit(u).query)
            v = q.get("uddg", [""])[0]
            return unquote(v)
        except Exception:
            return u
    return u


def _looks_ok(u: str) -> bool:
    if not u or not u.startswith("http"):
        return False
    host = urlparse(u).netloc.lower()
    if any(bad in host for bad in DISALLOW):
        return False
    return True


def _make_queries(name: str) -> List[str]:
    base = name.strip()
    ascii_name = _strip_accents(base)
    terms = [
        "digital identity",
        "self-sovereign identity",
        "decentralized identity",
        "verifiable credentials",
        "DID method",
        "wallet",
        "eIDAS",
        "Web of Trust",
        "official site",
    ]
    qs = []
    for t in terms:
        qs.append(f"{base} {t}")
        if ascii_name != base:
            qs.append(f"{ascii_name} {t}")
    # de-dupe while keeping order
    seen = set(); out=[]
    for q in qs:
        k = q.lower()
        if k not in seen:
            seen.add(k); out.append(q)
    return out


def _ddg_html(query: str, want: int) -> List[Dict[str,str]]:
    """Scrape DuckDuckGo HTML results."""
    url = "https://duckduckgo.com/html/"
    try:
        r = requests.get(url, params={"q": query}, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        out=[]
        for a in soup.select("a.result__a, a.result__url"):
            href = a.get("href","")
            href = _clean_ddg_url(href)
            if _looks_ok(href):
                title = a.get_text(strip=True)
                out.append({"url": href, "title": title})
            if len(out) >= want:
                break
        return out
    except Exception as e:
        print(f"[search] ddg html error: {e}")
        return []


def _bing_html(query: str, want: int) -> List[Dict[str,str]]:
    """Scrape Bing HTML results as a fallback."""
    url = "https://www.bing.com/search"
    try:
        r = requests.get(url, params={"q": query, "setlang": "en-US"}, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        out=[]
        for li in soup.select("li.b_algo h2 a"):
            href = li.get("href","")
            if _looks_ok(href):
                title = li.get_text(strip=True)
                out.append({"url": href, "title": title})
            if len(out) >= want:
                break
        return out
    except Exception as e:
        print(f"[search] bing html error: {e}")
        return []


def _dedupe_keep_order(items: List[Dict[str,str]]) -> List[Dict[str,str]]:
    seen = set(); out=[]
    for it in items:
        u = it.get("url","")
        if u not in seen:
            seen.add(u); out.append(it)
    return out


def search_project(name: str, out_dir: Path) -> List[Dict[str,str]]:
    print(f"[search] {name}: start (target={MAX_URLS})")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "urls.json"

    # manual seeds (if present)
    results: List[Dict[str,str]] = []
    manual = out_dir / "manual_urls.txt"
    if manual.exists():
        count=0
        for line in manual.read_text(encoding="utf-8").splitlines():
            u = line.strip()
            if u and _looks_ok(u):
                results.append({"url": u, "title": ""}); count+=1
        print(f"[search] {name}: manual seeds -> {count}")
    else:
        print(f"[search] {name}: manual seeds -> 0")

    queries = _make_queries(name)
    if not queries:
        print(f"[search] {name}: no queries generated")
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        return results

    for q in queries:
        if len(results) >= MAX_URLS:
            break
        time.sleep(BACKOFF)
        print(f"[search] {name}: query -> {q}")

        need = MAX_URLS - len(results)
        hits = _ddg_html(q, want=need)

        if not hits:
            time.sleep(BACKOFF)
            hits = _bing_html(q, want=need)

        results.extend(hits)
        results = _dedupe_keep_order(results)
        print(f"[search] {name}: found so far -> {len(results)}")

    print(f"[search] {name}: done, total urls={len(results)}")
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results
