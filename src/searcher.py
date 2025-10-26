# src/searcher.py
import os, json, time
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv

# Load .env so SEARCH_PROVIDER / SERPAPI_... are visible
load_dotenv()

# ----- Config (SerpAPI only in this minimal version) -----
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERPAPI_ENGINE = os.getenv("SERPAPI_ENGINE", "google").lower()  # google | bing | duckduckgo
MAX_URLS = int(os.getenv("MAX_URLS_PER_PROJECT", "6"))
BACKOFF = float(os.getenv("SEARCH_BACKOFF_SECONDS", "1.5"))

DISALLOW_HOSTS = ("facebook.com", "instagram.com", "tiktok.com", "pinterest.com", "reddit.com")

def _looks_ok(u: str) -> bool:
    if not u or not u.startswith("http"):
        return False
    host = urlparse(u).netloc.lower()
    if any(bad in host for bad in DISALLOW_HOSTS):
        return False
    return True

def _make_queries(name: str) -> List[str]:
    base = name.strip()
    quoted = f'"{base}"' if " " in base else base
    terms = [
        "digital identity", "self-sovereign identity", "decentralized identity",
        "verifiable credentials", "DID method", "wallet", "eIDAS", "official site",
    ]
    qs: List[str] = []
    for t in terms:
        qs.append(f"{base} {t}")
        qs.append(f"{quoted} {t}")
    qs += [f"{base} identity project", f"{base} site"]
    # de-dupe keep order
    seen, out = set(), []
    for q in qs:
        k = q.lower()
        if k not in seen:
            seen.add(k); out.append(q)
    return out

def _serpapi_search(query: str, want: int) -> List[Dict[str, str]]:
    if not SERPAPI_API_KEY:
        print("[search] serpapi: missing SERPAPI_API_KEY")
        return []
    url = "https://serpapi.com/search.json"
    params = {"q": query, "engine": SERPAPI_ENGINE, "api_key": SERPAPI_API_KEY, "num": max(10, want*2)}
    out: List[Dict[str, str]] = []
    try:
        r = requests.get(url, params=params, timeout=30)
        print(f"[search] serpapi {SERPAPI_ENGINE}: {r.status_code}")
        r.raise_for_status()
        j = r.json()
        for item in j.get("organic_results") or []:
            u = item.get("link") or item.get("url") or ""
            t = item.get("title") or ""
            if _looks_ok(u):
                out.append({"url": u, "title": t})
            if len(out) >= want:
                return out
        # Fallback blocks
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

def _dedupe_keep_order(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen, out = set(), []
    for it in items:
        u = it.get("url", "")
        if u not in seen:
            seen.add(u); out.append(it)
    return out

# ---------- PUBLIC API ----------
def search_project(name: str, out_dir: Path) -> List[Dict[str, str]]:
    """
    Return a list of {'url','title'} for the given project name, caching to urls.json.
    """
    print(f"[search] {name}: start (target={MAX_URLS}) [provider=serpapi-only]")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "urls.json"

    results: List[Dict[str, str]] = []

    # Manual seeds (optional)
    manual = out_dir / "manual_urls.txt"
    if manual.exists():
        count = 0
        for line in manual.read_text(encoding="utf-8").splitlines():
            u = (line or "").strip()
            if u and _looks_ok(u):
                results.append({"url": u, "title": ""}); count += 1
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
        hits = _serpapi_search(q, want=need)

        results.extend(hits)
        results = _dedupe_keep_order(results)
        print(f"[search] {name}: found so far -> {len(results)}")

    print(f"[search] {name}: done, total urls={len(results)}")
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results
