# src/app/workers/extractor.py
from __future__ import annotations
import os, json, logging, re
from typing import List, Dict, Any, Tuple
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.schema import name_header, coerce_to_headers
from app.utils.logger import get_logger

log = get_logger("extractor")

SYSTEM = (
    "You are a careful data extraction agent for a Digital Identity codebook. "
    "You MUST return EXACTLY one JSON object enclosed in <JSON>...</JSON>. "
    "Every non-empty field MUST cite an evidence URL present in the provided context."
)

def _write_cache(project: str, fname: str, text: str) -> None:
    root = Path(__file__).resolve().parents[3]
    d = root / "data" / "cache" / project
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(text, encoding="utf-8")

def _chat_openai(messages: List[Dict[str, str]], model: str, max_tokens: int) -> str:
    """
    OpenAI Chat Completions call with extra guards.
    Returns the raw content string.
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # NB: gpt-4o-mini supports JSON-ish, but we enforce tags in the prompt anyway.
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
    )
    content = (resp.choices[0].message.content or "").strip()
    return content

def _extract_snippets(pages: List[Dict[str, Any]], limit: int = 3, chars: int = 1200) -> List[str]:
    snaps = []
    for p in pages:
        t = (p.get("text") or "").strip()
        u = (p.get("url") or "").strip()
        if not t: continue
        snaps.append(f"[URL]{u}\n{t[:chars]}")
        if len(snaps) >= limit: break
    return snaps

def _parse_llm_json(raw: str) -> Tuple[Dict[str, Any] | None, str]:
    if not raw: return None, "empty"
    # 1) exact JSON
    try:
        j = json.loads(raw)
        return j if isinstance(j, dict) else None, "json.loads"
    except Exception:
        pass
    # 2) <JSON>...</JSON>
    m = re.search(r"<JSON>(.*?)</JSON>", raw, flags=re.DOTALL | re.IGNORECASE)
    if m:
        chunk = m.group(1).strip()
        try:
            j = json.loads(chunk)
            return j if isinstance(j, dict) else None, "xml-tag-salvage"
        except Exception:
            pass
    # 3) outermost braces salvage
    s, e = raw.find("{"), raw.rfind("}")
    if s != -1 and e > s:
        chunk = raw[s:e+1]
        try:
            j = json.loads(chunk)
            return j if isinstance(j, dict) else None, "curly-salvage"
        except Exception:
            pass
    return None, "failed"

def _seed_row(project: str, headers: List[str], pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    row = {h: "" for h in headers}
    row[name_header(headers)] = project
    row["_evidence"] = []
    if pages:
        first = pages[0]
        url = (first.get("url") or "").strip()
        txt = (first.get("text") or "").strip()
        # Website
        for h in headers:
            if h.lower() == "website" and url:
                row[h] = url
                row["_evidence"].append({"field": "Website","value": url,"source_url": url,"source_type": "webpage","confidence": "medium"})
                break
        # Brief
        snippet = (txt[:240] + "…") if len(txt) > 240 else txt
        for h in headers:
            if h.lower() in ("description", "project description", "brief"):
                row[h] = snippet
                if url:
                    row["_evidence"].append({"field": h, "value": snippet, "source_url": url, "source_type": "webpage", "confidence": "low"})
                break
    return row

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
def extract_record(project: str, headers: List[str], pages: List[Dict[str, Any]], *, provider: str, model: str, max_output_tokens: int) -> Dict[str, Any]:
    log.info("[extract] %s: pages received=%d", project, len(pages))
    snaps = _extract_snippets(pages, limit=3, chars=1200)
    log.info("[extract] %s: using %d page snippets", project, len(snaps))

    seeds = _seed_row(project, headers, pages)
    if not snaps:
        log.info("[extract] %s: no context → returning seeds only", project)
        return seeds

    context = "\n\n".join(snaps)
    payload = {
        "project": project,
        "headers": headers,
        "instructions": (
            "Return ONLY one JSON object enclosed in <JSON>...</JSON>.\n"
            "Set 'Product Name' exactly to the `project` value.\n"
            "Allowed Status: [Announced,Pilot,Launched,Discontinued].\n"
            "Ternary fields: [True,False,Failed to disclose]. Year fields are 4 digits.\n"
            "Every non-empty field MUST add an object into _evidence with keys: "
            "{field, value, source_url, source_type='webpage', confidence} and the URL MUST be present in context."
        ),
        "context": context,
    }
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": json.dumps(payload, ensure_ascii=False)},
    ]

    log.info("[extract] %s: calling LLM… (provider=%s, model=%s)", project, provider, model)
    try:
        raw = _chat_openai(messages, model=model, max_tokens=max_output_tokens)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        log.error("[extract] %s: LLM call failed → %s", project, err)
        _write_cache(project, "llm_error.txt", err)
        raw = ""
    _write_cache(project, "llm_raw.json", raw)
    log.info("[extract] %s: raw chars=%d", project, len(raw))

    obj, how = _parse_llm_json(raw)
    if not obj:
        log.info("[map] %s: parse failed (%s); using seeds only", project, how)
        return seeds
    log.info("[map] %s: parse ok via %s; keys=%s", project, how, list(obj.keys())[:12])

    # Normalize + merge (LLM wins only where seeds empty)
    seeds_n = coerce_to_headers(seeds, headers, project_name=project) or {}
    llm_n   = coerce_to_headers(obj,    headers, project_name=project) or {}
    data: Dict[str, Any] = {h: seeds_n.get(h, "") for h in headers}
    mapped = 0
    for h in headers:
        sv = seeds_n.get(h, "")
        lv = llm_n.get(h, "")
        if (isinstance(sv, str) and not sv.strip()) and (isinstance(lv, str) and lv.strip()):
            data[h] = lv; mapped += 1
    log.info("[map] %s: mapped=%d", project, mapped)

    # merge evidence
    ev: List[Dict[str, Any]] = []
    if isinstance(seeds_n.get("_evidence"), list): ev += seeds_n["_evidence"]
    if isinstance(llm_n.get("_evidence"),   list): ev += llm_n["_evidence"]
    data["_evidence"] = ev

    # always ensure name column populated
    ncol = name_header(headers)
    if not (isinstance(data.get(ncol), str) and data.get(ncol).strip()):
        data[ncol] = project

    filled = sum(1 for k,v in data.items() if k != "_evidence" and isinstance(v, str) and v.strip())
    log.info("[extract] %s: fields filled=%d", project, filled)
    return data
