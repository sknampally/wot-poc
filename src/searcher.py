# src/searcher.py
import os, json, time, re
from pathlib import Path
from typing import List, Dict, Tuple
import requests

# -----------------------
# Runtime configuration
# -----------------------
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "serpapi").lower()  # serpapi | ddghtml | binghtml
SERPAPI_KEY = os.getenv("SERPAPI_API_KEY", "")
MAX_URLS_PER_PROJECT = int(os.getenv("MAX_URLS_PER_PROJECT", "2"))
REGION = os.getenv("SEARCH_REGION", "us-en")
SAFESEARCH = os.getenv("SEARCH_SAFE", "moderate")

# Disallow low-signal hosts / file types
DISALLOW_HOSTS = {
    "linkedin.com", "www.linkedin.com", "x.com", "twitter.com",
    "facebook.com", "www.facebook.com", "instagram.com", "t.co"
}
DISALLOW_SUFFIXES = (".pdf", ".ppt", ".pptx", ".doc", ".docx", ".zip", ".rar")

UA = os.getenv("SEARCH_UA", "Mozilla/5.0 (compatible; wot-poc-bot/0.1; +https://example.org/)")

def _allowed(u: str) -> bool:
    try:
        from urllib.parse import urlparse
        p = urlparse(u)
        host = (p.netloc or "").lower()
        if not host:
            return False
        if any(host.endswith(h) for h in DISALLOW_HOSTS):
            return False
        clean = u.lower().split("?")[0]
        if clean.endswith(DISALLOW_SUFFIXES):
            return False
        # basic sanity
        if not p.scheme.startswith("http"):
            return False
        return True
    except Exception:
        return False

def _read_manual_seeds(proj_dir: Path) -> List[str]:
    seeds = []
    f = proj_dir / "manual_urls.txt"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            u = line.strip()
            if u and not u.startswith("#") and _allowed(u):
                seeds.append(u)
    return seeds

def _save_urls(urls: List[Dict[str, str]], out_path: Path) -> None:
    out_path.write_text(json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8")

def _add(result_list: List[Dict[str, str]], url: str, title: str) -> None:
    if not url:
        return
    if not _allowed(url):
        return
    # de-dupe by normalized URL
    norm = re.sub(r"#.*$", "", url.strip())
    if any(re.sub(r"#.*$", "", r.get("url", "")) == norm for r in result_list):
        return
    result_list.append({"url": url, "title": title or ""})

def _make_queries(name: str) -> List[str]:
    base = name.strip()
    quoted = f'"{base}"' if " " in base else base

    qs: List[str] = []
    # core
    for t in [
        "digital identity", "self-sovereign identity", "decentralized identity",
        "verifiable credentials", "wallet", "official site", "eIDAS"
    ]:
        qs += [f"{base} {t}", f"{quoted} {t}"]

    # site: hints
    for host_hint in [base.lower(), f"{base.lower()}.com", f"{base.lower()}.io"]:
        qs += [
            f'site:{host_hint} {base} "digital identity"',
            f'site:{host_hint} {base} "verifiable credentials"',
        ]

    # extras
    extras = ["DID method", "W3C VC", "EUDI", "trust framework", "open source"]
    for t in extras:
        qs += [f"{base} {t}", f"{quoted} {t}"]

    # de-dupe keeping order
    seen, out = set(), []
    for q in qs:
        k = q.lower()
        if k not in seen:
            seen.add(k); out.append(q)
    return out

# -----------------------
# Providers
# -----------------------
def _search_serpapi(q: str, num: int = 10) -> List[Tuple[str, str]]:
    if not SERPAPI_KEY:
        return []
    params = {
        "engine": "google",
        "q": q,
        "num": num,
        "api_key": SERPAPI_KEY,
        "hl": "en",
        "safe": "active" if SAFESEARCH.lower() != "off" else "off",
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30, headers={"User-Agent": UA})
    print(f"[search] serpapi google: {r.status_code}")
    if r.status_code != 200:
        return []
    data = r.json()
    out: List[Tuple[str, str]] = []
    for item in data.get("organic_results", []):
        url = item.get("link") or item.get("url") or ""
        title = item.get("title") or ""
        if url:
            out.append((url, title))
    return out

def _search_ddg_html(q: str) -> List[Tuple[str, str]]:
    # lightweight, but DDG rate-limits easily—kept as fallback only
    urls = []
    try:
        r = requests.get("https://duckduckgo.com/html/", params={"q": q}, timeout=20, headers={"User-Agent": UA})
        print(f"[search] ddg html: {r.status_code} {len(r.content)} bytes")
        if r.status_code != 200:
            return urls
        # naive scraping: extract hrefs
        for m in re.finditer(r'href="(https?://[^"]+)"', r.text):
            url = m.group(1)
            title = ""
            urls.append((url, title))
    except Exception as e:
        print(f"[search] ddg html error: {e}")
    return urls

def _search_bing_html(q: str) -> List[Tuple[str, str]]:
    urls = []
    try:
        r = requests.get("https://www.bing.com/search", params={"q": q}, timeout=20, headers={"User-Agent": UA})
        print(f"[search] bing html: {r.status_code} {len(r.content)} bytes")
        if r.status_code != 200:
            return urls
        for m in re.finditer(r'<h2><a href="(https?://[^"]+)"', r.text):
            url = m.group(1)
            title = ""
            urls.append((url, title))
    except Exception as e:
        print(f"[search] bing html error: {e}")
    return urls

# -----------------------
# Public entrypoint
# -----------------------
def search_project(name: str, proj_dir: Path) -> List[Dict[str, str]]:
    target = MAX_URLS_PER_PROJECT
    print(f"[search] {name}: start (target={target}) [provider={'serpapi-only' if SEARCH_PROVIDER=='serpapi' else SEARCH_PROVIDER}]")

    urls_json = proj_dir / "urls.json"
    urls: List[Dict[str, str]] = []
    gathered: List[Dict[str, str]] = []

    # Manual seeds first
    seeds = _read_manual_seeds(proj_dir)
    print(f"[search] {name}: manual seeds -> {len(seeds)}")
    for u in seeds:
        _add(gathered, u, "")

    # If we already have enough from seeds, write & return
    if len(gathered) >= target:
        _save_urls(gathered[:target], urls_json)
        print(f"[search] {name}: done, total urls={len(gathered[:target])}")
        return gathered[:target]

    queries = _make_queries(name)
    for q in queries:
        if len(gathered) >= target:
            break

        print(f"[search] {name}: query -> {q}")

        found: List[Tuple[str, str]] = []
        if SEARCH_PROVIDER == "serpapi":
            found = _search_serpapi(q, num=10)
        else:
            # try ddg + bing HTML (no keys)
            found = _search_ddg_html(q)
            if len(found) < 3:
                found += _search_bing_html(q)

        print(f"[search] {name}: found so far -> {len(gathered)}")

        for u, title in found:
            if len(gathered) >= target:
                break
            _add(gathered, u, title)

        # small pause to be polite
        time.sleep(0.4)

    _save_urls(gathered, urls_json)
    print(f"[search] {name}: done, total urls={len(gathered)}")
    return gathered
