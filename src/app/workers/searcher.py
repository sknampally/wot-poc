# src/app/workers/searcher.py
from __future__ import annotations
import os, re, json, logging
from typing import List, Dict
from pathlib import Path

log = logging.getLogger("searcher")

def _slug(s: str) -> str:
    return re.sub(r"\s+", "-", s.strip().lower())

def _guess_domains(project: str) -> List[str]:
    base = re.sub(r"[^a-z0-9]+", "", project.lower())
    if not base:
        base = _slug(project)
    doms = [f"https://{base}.io/", f"https://{base}.com/", f"https://{base}.org/"]
    # Lightweight alternates
    alts = [
        f"https://{base}.io/blog/",
        f"https://{base}.io/docs/",
        f"https://{base}.com/blog/",
        f"https://{base}.org/news/",
        f"https://{base}.io/ssi/",
        f"https://{base}.io/developers/",
        f"https://{base}.io/solutions/",
    ]
    return doms + alts

def _curated_paths(project: str) -> List[str]:
    slug = _slug(project)
    return [
        # helpful generic places for DID/SSI vendors
        f"https://github.com/{slug}",
        f"https://medium.com/@{slug}",
        f"https://www.linkedin.com/company/{slug}/",
        f"https://{slug}.substack.com/",
        # docs-ish
        f"https://docs.{slug}.io/",
        f"https://learn.{slug}.io/",
        f"https://{slug}.io/blog/",
        f"https://{slug}.io/tag/self-sovereign-identity/",
        f"https://{slug}.io/tag/verifiable-credentials/",
    ]

def _manual_seeds_dir(project: str) -> List[str]:
    # allow per-project manual seeds under data/cache/<Project>/manual_urls.txt
    # one URL per line
    root = Path(__file__).resolve().parents[3]  # repo root
    path = root / "data" / "cache" / project / "manual_urls.txt"
    if path.exists():
        try:
            lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            return lines
        except Exception:
            return []
    return []

def search_urls(project: str, target_count: int = 6) -> List[Dict[str, str]]:
    """
    Return a list of {'url':..., 'source':'manual|guess|curated'} without SerpAPI.
    Honors env MAX_URLS_PER_PROJECT if present.
    """
    max_urls = int(os.getenv("MAX_URLS_PER_PROJECT", str(target_count)))
    urls: List[Dict[str, str]] = []

    manual = _manual_seeds_dir(project)
    for u in manual:
        urls.append({"url": u, "source": "manual"})
    if len(urls) >= max_urls:
        log.info("[search] %s: manual seeds -> %d", project, len(urls))
        return urls[:max_urls]

    guesses = _guess_domains(project)
    for u in guesses:
        if all(u != x["url"] for x in urls):
            urls.append({"url": u, "source": "guess"})
    if len(urls) >= max_urls:
        log.info("[search] %s: guesses -> %d", project, len(urls))
        return urls[:max_urls]

    curated = _curated_paths(project)
    for u in curated:
        if all(u != x["url"] for x in urls):
            urls.append({"url": u, "source": "curated"})
    urls = urls[:max_urls]
    log.info("[search] %s: done, total urls=%d", project, len(urls))

    # save urls.json for traceability
    root = Path(__file__).resolve().parents[3]
    outdir = root / "data" / "cache" / project
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "urls.json").write_text(json.dumps(urls, indent=2), encoding="utf-8")
    return urls
