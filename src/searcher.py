import json, os, time, random
from pathlib import Path
from typing import List, Dict
from duckduckgo_search import DDGS
from duckduckgo_search.exceptions import RatelimitException
from dotenv import load_dotenv

load_dotenv()
MAX_URLS = int(os.getenv("MAX_URLS_PER_PROJECT", "10"))
BACKOFF = float(os.getenv("SEARCH_BACKOFF_SECONDS", "3"))
RETRIES = int(os.getenv("MAX_SEARCH_RETRIES", "3"))
# --- Configuration from environment ---
REGION = os.getenv("SEARCH_REGION", "us-en")
SAFESEARCH = os.getenv("SEARCH_SAFESEARCH", "moderate")
BACKOFF = float(os.getenv("SEARCH_BACKOFF_SECONDS", "2"))
MAX_URLS = int(os.getenv("MAX_URLS_PER_PROJECT", "3"))
MAX_RETRIES = int(os.getenv("MAX_SEARCH_RETRIES", "3"))


# You can edit or extend the query list if needed
BASE_QUERIES = [
    "{name} digital identity",
    "{name} self sovereign identity",
    "{name} verifiable credentials",
    "{name} decentralized identity",
    "{name} government press release",
    "{name} white paper",
    "{name} github",
]

def _run_ddg_query(ddgs: DDGS, query: str, max_results: int) -> List[Dict]:
    """
    Run a single DuckDuckGo query with retries and backoff.
    Returns a list of results [{title, url, body}, ...].
    """
    attempt = 0
    while True:
        try:
            # ddgs.text returns a generator; convert to list
            results = list(ddgs.text(query, max_results=max_results))
            return results
        except RatelimitException:
            attempt += 1
            if attempt > RETRIES:
                # Give back an empty list so we can continue with other queries
                return []
            time.sleep(BACKOFF * attempt)
        except Exception:
            # Network hiccup — brief wait then continue
            attempt += 1
            if attempt > RETRIES:
                return []
            time.sleep(1.0 * attempt)

def _manual_seed_if_present(name: str, out_dir: Path) -> List[Dict]:
    """
    If you create a file data/cache/<Project>/manual_urls.txt with one URL per line,
    we will use those first (helps when search is flaky or you already know sources).
    """
    mfile = out_dir / "manual_urls.txt"
    results = []
    if mfile.exists():
        for line in mfile.read_text(encoding="utf-8").splitlines():
            u = line.strip()
            if u and not u.startswith("#"):
                results.append({"query": "manual", "title": "", "url": u})
    return results

def search_project(name: str, out_dir: Path) -> List[Dict]:
    """
    Searches DuckDuckGo for candidate URLs, with backoff and retry handling for rate limits.
    Writes urls.json to cache and returns the list.
    Also supports an optional manual seed file: manual_urls.txt
    """
    print(f"[search] {name}: start (target={MAX_URLS})")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "urls.json"

    # If urls.json already exists and has enough results, reuse it to avoid extra calls
    if out_path.exists():
        try:
            cached = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(cached, list) and len(cached) >= min(MAX_URLS, 3):
                return cached[:MAX_URLS]
        except Exception:
            pass

    # Start with manual seeds if provided
    seen = set()
    results_all: List[Dict] = []
    for item in _manual_seed_if_present(name, out_dir):
        u = item["url"]
        if u not in seen:
            seen.add(u)
            results_all.append(item)
            if len(results_all) >= MAX_URLS:
                out_path.write_text(json.dumps(results_all, indent=2), encoding="utf-8")
                return results_all
    
    print(f"[search] {name}: manual seeds -> {len(results_all)}")
    # Randomize query order to spread load
    queries = BASE_QUERIES[:]
    random.shuffle(queries)

    print(f"[search] {name}: querying DDG (region={REGION}, safe={SAFESEARCH})")
    with DDGS() as ddgs:
        for q in queries:
            query = q.format(name=name)
            # Be gentle: small sleep between queries to reduce 202 rate-limit responses
            time.sleep(BACKOFF)

            results = _run_ddg_query(ddgs, query, max_results=MAX_URLS)
            for r in results:
                url = r.get("href") or r.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                results_all.append({
                    "query": query,
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("body", ""),
                })
                if len(results_all) >= MAX_URLS:
                    break
            if len(results_all) >= MAX_URLS:
                break

    print(f"[search] {name}: done, total urls={len(results_all)}")
    # Persist to cache
    out_path.write_text(json.dumps(results_all, indent=2), encoding="utf-8")
    return results_all
