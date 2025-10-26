# src/searcher.py
import os, json, time, unicodedata
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse, urlsplit, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

# ---------- Env & defaults ----------
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "ddghtml").lower()  # ddghtml | serpapi
BACKOFF = float(os.getenv("SEARCH_BACKOFF_SECONDS", "2"))
MAX_URLS = int(os.getenv("MAX_URLS_PER_PROJECT", "6"))

# SerpAPI config
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERPAPI_ENGINE = os.getenv("SERPAPI_ENGINE", "google").lower()  # google | bing | duckduckgo

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
DISALLOW = ("facebook.com","instagram.com","tiktok.com","pinterest.com","reddit.com")
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
    quoted = f'"{base}"' if " " in base else base
    terms = [
        "digital identity",
        "self-sovereign identity",
        "decentralized identity",
        "verifiable credentials",
        "DID method",
        "wallet",
        "eIDAS",
        "official site",
    ]
    qs = []
    for t in terms:
        qs.append(f"{base} {t}")
        if ascii_name != base:
            qs.append(f"{ascii_name} {t}")
        qs.append(f'{quoted} {t}')
    qs += [f"{base} identity project", f"{ascii_name} site"]
    # de-dupe while keeping order
    seen = set(); out=[]
    for q in qs:
        k = q.lower()
        if k not in seen:
            seen.add(k); out.append(q)
    return out


# ---------- ddghtml path (DuckDuckGo + Bing scraping) ----------
def _ddg_html(query: str, want: int) -> List[Dict[str,str]]:
    url = "https://duckduckgo.com/html/"
    out = []
    try:
        r = requests.get(url, params={"q": query}, headers=HEADERS, timeout=20)
        print(f"[search] ddg html: {r.status_code} {len(r.content)} bytes")
        soup = BeautifulSoup(r.text, "html.parser")
        selectors = [
            "a.result__a",
            "a.result__url",
            ".result__title a",
            "a.js-result-title-link",
        ]
        for sel in selectors:
            for a in soup.select(sel):
                href = a.get("href","")
                href = _clean_ddg_url(href)
                if _looks_ok(href):
                    title = a.get_text(strip=True)
                    out.append({"url": href, "title": title})
                if len(out) >= want:
                    return out
        return out
    except Exception as e:
        print(f"[search] ddg html error: {e}")
        return out

def _ddg_lite(query: str, want: int) -> List[Dict[str,str]]:
    url = "https://duckduckgo.com/lite/"
    out = []
    try:
        r = requests.get(url, params={"q": query}, headers=HEADERS, timeout=20)
        print(f"[search] ddg lite: {r.status_code} {len(r.content)} bytes")
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("table tr td:nth-child(2) a"):
            href = a.get("href","")
            href = _clean_ddg_url(href)
            if _looks_ok(href):
                title = a.get_text(strip=True)
                out.append({"url": href, "title": title})
            if len(out) >= want:
                break
        return out
    except Exception as e:
        print(f"[search] ddg lite error: {e}")
        return out

def _bing_html(query: str, want: int) -> List[Dict[str,str]]:
    url = "https://www.bing.com/search"
    out = []
    try:
        r = requests.get(url, params={"q": query, "setlang": "en-US"}, headers=HEADERS, timeout=20)
        print(f"[search] bing html: {r.status_code} {len(r.content)} bytes")
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("li.b_algo h2 a"):
            href = a.get("href","")
            if _looks_ok(href):
                title = a.get_text(strip=True)
                out.append({"url": href, "title": title})
            if len(out) >= want:
                break
        return out
    except Exception as e:
        print(f"[search] bing html error: {e}")
        return out

def _ddghtml_search(query: str, want: int) -> List[Dict[str,str]]:
    hits = _ddg_html(query, want)
    if not hits:
        time.sleep(BACKOFF)
        hits = _ddg_lite(query, want)
    if not hits:
        time.sleep(BACKOFF)
        hits = _bing_html(query, want)
    return hits


# ---------- serpapi path ----------
def _serpapi_search(query: str, want: int) -> List[Dict[str, str]]:
    if not SERPAPI_API_KEY:
        print("[search] serpapi: missing SERPAPI_API_KEY")
        return []
    engine = SERPAPI_ENGINE  # google | bing | duckduckgo
    url = "https://serpapi.com/search.json"
    params = {"q": query, "engine": engine, "api_key": SERPAPI_API_KEY, "num": max(10, want*2)}
    out: List[Dict[str, str]] = []
    try:
        r = requests.get(url, params=params, timeout=30)
        print(f"[search] serpapi {engine}: {r.status_code}")
        j = r.json()
        # Prefer organic results
        organic = j.get("organic_results") or []
        for item in organic:
            u = item.get("link") or item.get("url") or ""
            t = item.get("title") or ""
            if _looks_ok(u):
                out.append({"url": u, "title": t})
            if len(out) >= want:
                return out
        # Fallback to news or other blocks
        for block_name in ("news_results", "top_stories", "inline_videos", "answer_box"):
            blk = j.get(block_name) or []
            if isinstance(blk, dict):
                blk = [blk]
            for item in blk:
                u = item.get("link") or item.get("url") or ""
                t = item.get("title") or ""
                if _looks_ok(u):
                    out.append({"url": u, "title": t})
                if len(out) >= want:
                    return out
        return out
    except Exception as e:
        print(f"[search] serpapi error: {e}")
        return out


def _dedupe_keep_order(items: List[Dict[str,str]]) -> List[Dict[str,str]]:
    seen = set(); out=[]
    for it in items:
        u = it.get("url","")
        if u not in seen:
            seen.add(u); out.append(it)
    return out


def search_project(name: str, out_dir: Path) -> List[Dict[str,str]]:
    print(f"[search] {name}: start (target={MAX_URLS}) [provider={SEARCH_PROVIDER}]")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "urls.json"

    # manual seeds (if any)
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
        if SEARCH_PROVIDER == "serpapi":
            hits = _serpapi_search(q, want=need)
        else:
            hits = _ddghtml_search(q, want=need)

        results.extend(hits)
        results = _dedupe_keep_order(results)
        print(f"[search] {name}: found so far -> {len(results)}")

    print(f"[search] {name}: done, total urls={len(results)}")
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results
