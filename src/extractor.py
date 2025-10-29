# src/extractor.py
from __future__ import annotations
import os, re, json, requests
from typing import Dict, Any, List, Tuple
from pathlib import Path
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from schema import normalize_status, normalize_fd, normalize_year, _name_header

load_dotenv()

# Provider selection can be overridden at runtime by env set in main.py
PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower().strip()

# --- OpenAI config ---
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "1200"))

# --- Ollama config ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

SYSTEM = (
    "You are an extraction agent for digital identity (SSI/DI) projects.\n"
    "Return JSON ONLY between the tags <JSON> and </JSON>. No prose, no markdown.\n"
    "Keys MUST match the provided headers exactly.\n"
    "Rules:\n"
    "• Use ONLY facts in the provided context.\n"
    "• If a field is present, fill it; else set exactly 'Failed to disclose'.\n"
    "• Never invent values.\n"
    "• For every non-empty field, append an object to `_evidence` with:\n"
    "  {\"field\":\"<header>\", \"value\":\"<copied value>\", \"source_url\":\"<URL from context>\", \"source_type\":\"webpage\", \"confidence\":\"low|medium|high\"}\n"
    "• Status ∈ [Announced,Pilot,Launched,Discontinued].\n"
    "• Ternary fields ∈ [True,False,Failed to disclose].\n"
    "• Year fields must be 4-digit when present.\n"
)

# -------------------- low-level LLM calls --------------------

def _strip_code_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s

def parse_llm_json(raw: str) -> dict:
    """
    1) Prefer content between <JSON>...</JSON>
    2) Strip code fences if present
    3) Try direct json.loads
    4) Salvage with first '{' ... last '}'
    """
    if not raw:
        return {}

    # prefer explicit tags
    m = re.search(r"<JSON>(.*?)</JSON>", raw, flags=re.DOTALL|re.IGNORECASE)
    s = m.group(1) if m else raw

    s = _strip_code_fences(s)

    # standard parse
    try:
        return json.loads(s)
    except Exception:
        pass

    # salvage between outer braces
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        frag = s[i:j+1]
        try:
            return json.loads(frag)
        except Exception:
            # normalize smart quotes to straight quotes
            t = (frag
                 .replace("“", "\"").replace("”", "\"")
                 .replace("’", "'").replace("‘", "'"))
            try:
                return json.loads(t)
            except Exception:
                return {}
    return {}

def _ollama_chat_json(messages: List[Dict[str, str]], model: str) -> str:
    url = f"{OLLAMA_HOST}/api/chat"
    attempts = [
        {"num_predict": 450, "num_ctx": 2048, "timeout": 90},
        {"num_predict": 280, "num_ctx": 1536, "timeout": 75},
        {"num_predict": 180, "num_ctx": 1024, "timeout": 60},
    ]
    for i, cfg in enumerate(attempts, start=1):
        try:
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_ctx": cfg["num_ctx"],
                    "num_predict": cfg["num_predict"],
                },
            }
            print(f"[ollama] attempt {i}/3 → ctx={cfg['num_ctx']} predict={cfg['num_predict']} timeout={cfg['timeout']}s")
            r = requests.post(url, json=payload, timeout=cfg["timeout"])
            r.raise_for_status()
            j = r.json()
            content = j.get("message", {}).get("content", "") or j.get("response", "")
            if content and content.strip():
                return content
        except requests.exceptions.ReadTimeout:
            print(f"[ollama] ReadTimeout on attempt {i}")
        except Exception as e:
            body = ""
            try: body = r.text[:600]  # type: ignore
            except: pass
            raise RuntimeError(f"Ollama error: {e}; body={body}") from e
    return ""

def _openai_chat_json(messages, model: str, max_tokens: int) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "temperature": OPENAI_TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": messages,
        "max_tokens": max_tokens,  # <-- correct field name
    }
    r = requests.post(url, json=payload, headers=headers, timeout=120)
    if r.status_code >= 400:
        body = ""
        try: body = r.text[:1200]
        except Exception: pass
        raise requests.HTTPError(f"{r.status_code} {r.reason}: {body}")
    return r.json()["choices"][0]["message"]["content"]

def _chat_json(messages, provider: str, model: str, max_tokens: int) -> str:
    if provider == "openai":
        if not OPENAI_KEY:
            raise RuntimeError("OPENAI_API_KEY not set but LLM_PROVIDER=openai")
        return _openai_chat_json(messages, model, max_tokens)
    return _ollama_chat_json(messages, model)

# -------------------- JSON repair/parse --------------------

def _json_load_strict(s: str) -> Dict[str, Any] | None:
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def _salvage_to_json_obj(s: str) -> Dict[str, Any] | None:
    # Grab the outermost { ... } region
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(s[start:end+1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
    return None

def parse_llm_json(raw: str) -> Tuple[Dict[str, Any] | None, str]:
    obj = _json_load_strict(raw)
    if obj is not None:
        return obj, "strict"
    obj = _salvage_to_json_obj(raw)
    if obj is not None:
        return obj, "regex-salvage"
    return None, "failed"

# -------------------- heuristics: pre-extract from text --------------------

_STATUS_MAP = {
    "launched": "Launched",
    "live": "Launched",
    "in production": "Launched",
    "pilot": "Pilot",
    "beta": "Pilot",
    "announced": "Announced",
    "discontinued": "Discontinued",
    "sunset": "Discontinued",
}

_TECH_HINTS = [
    "hyperledger", "indy", "aries", "did:", "verifiable credential",
    "veramo", "cheqd", "cosmos", "tendermint", "polygon", "zkp", "zero-knowledge",
    "trust registry", "wallet", "holder", "issuer", "verifier", "did method"
]

def _domain_of(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", re.findall(r"https?://([^/]+)", url)[0]).lower()
    except Exception:
        return ""

def _pick_main_domain(pages: List[Dict[str, Any]]) -> str:
    # choose the most frequent non-social domain
    counts: Dict[str, int] = {}
    for p in pages:
        d = _domain_of(p.get("url", ""))
        if not d: continue
        if any(d.endswith(s) for s in ("linkedin.com", "x.com", "twitter.com", "facebook.com")):
            continue
        counts[d] = counts.get(d, 0) + 1
    if not counts: return ""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

def _select_snippets(pages: List[Dict[str, Any]], main_domain: str, max_pages: int = 3, max_chars: int = 1200) -> List[str]:
    prioritized = []
    # prefer main domain, docs., blog. on same base, then everything else
    for p in pages:
        u = p.get("url", ""); t = (p.get("text") or "").strip()
        if not t: continue
        d = _domain_of(u)
        score = 0
        if main_domain and (d == main_domain or d.endswith("." + main_domain)):
            score += 5
            if any(seg in u for seg in ("/docs", "/documentation", "/blog", "/product", "/developers")):
                score += 2
        if "europa.eu" in d or "gov." in d:
            score += 1
        prioritized.append((score, u, t))
    if not prioritized:
        for p in pages:
            u = p.get("url", ""); t = (p.get("text") or "").strip()
            if t: prioritized.append((0, u, t))
    prioritized.sort(key=lambda r: (-r[0], len(r[2])), reverse=False)
    out = []
    for _, u, t in prioritized[:max_pages]:
        out.append(f"[URL]{u}\n{t[:max_chars]}")
    return out

def _evidence(field: str, value: str, url: str, conf: str = "medium") -> Dict[str, str]:
    return {
        "field": field,
        "value": value,
        "source_url": url,
        "source_type": "webpage",
        "confidence": conf
    }

def _pre_extract(pages: List[Dict[str, Any]], headers: List[str], project_name: str) -> Dict[str, Any]:
    """
    Heuristic extraction BEFORE LLM:
    - Status (Announced/Pilot/Launched/Discontinued)
    - Announcement / Launch years
    - ZKP, Exportable creds, Key storage
    - Targets: Holders/Issuers/Verifiers
    - Website
    - Tech Stack Descriptions (keywords)
    Everything else left blank for the LLM to fill.
    """
    hset = {h.lower(): h for h in headers}
    out: Dict[str, Any] = {h: "" for h in headers}
    out[_name_header(headers)] = project_name
    out["_evidence"] = []

    # website = first page on main domain
    main_domain = _pick_main_domain(pages)
    if "website" in hset and main_domain:
        for p in pages:
            u = p.get("url", "")
            if _domain_of(u) == main_domain:
                out[hset["website"]] = u
                out["_evidence"].append(_evidence(hset["website"], u, u))
                break

    # scan text
    year_re = re.compile(r"(20\d{2})")
    launch_re = re.compile(r"(launch\w*|went live|live since|in production)\W+(?:in\s*)?(20\d{2})", re.I)
    announce_re = re.compile(r"(announc\w*)\W+(?:in\s*)?(20\d{2})", re.I)

    status_found = None
    launch_year = None
    announce_year = None
    zkp = None
    exportable = None
    key_storage = None
    tgt_h = None; tgt_i = None; tgt_v = None
    tech_hits: List[str] = []

    for p in pages:
        u = p.get("url", ""); txt = (p.get("text") or "").lower()
        if not txt: continue

        # status
        for k,v in _STATUS_MAP.items():
            if k in txt:
                status_found = v
                out["_evidence"].append(_evidence("Status", v, u))
                break

        # years
        m = launch_re.search(txt)
        if m and not launch_year:
            launch_year = m.group(2)
            out["_evidence"].append(_evidence("Project Launch Date", launch_year, u))
        m = announce_re.search(txt)
        if m and not announce_year:
            announce_year = m.group(2)
            out["_evidence"].append(_evidence("Project Announcement Date", announce_year, u))

        # ZKP
        if zkp is None:
            if "zero-knowledge" in txt or "zkp" in txt:
                zkp = "True"; out["_evidence"].append(_evidence("Endorses/Uses ZKP", "True", u))
        # exportable credentials (very loose heuristic)
        if exportable is None:
            if "export credential" in txt or "exportable credential" in txt or "download credential" in txt:
                exportable = "True"; out["_evidence"].append(_evidence("Has Exportable Credentials", "True", u))
        # key storage keywords
        if key_storage is None:
            if "non-custodial" in txt or "self-custody" in txt or "on-device" in txt:
                key_storage = "True"; out["_evidence"].append(_evidence("Credential and Key Storage", "True", u))

        # targets
        if tgt_h is None and "holder" in txt:
            tgt_h = "True"; out["_evidence"].append(_evidence("Targets Holders", "True", u))
        if tgt_i is None and "issuer" in txt:
            tgt_i = "True"; out["_evidence"].append(_evidence("Targets Issuers", "True", u))
        if tgt_v is None and "verifier" in txt:
            tgt_v = "True"; out["_evidence"].append(_evidence("Targets Verifiers", "True", u))

        # tech stack
        for kw in _TECH_HINTS:
            if kw in txt and kw not in tech_hits:
                tech_hits.append(kw)

    # assign
    if status_found and "status" in hset:
        out[hset["status"]] = status_found
    if launch_year and "project launch date" in hset:
        out[hset["project launch date"]] = launch_year
    if announce_year and "project announcement date" in hset:
        out[hset["project announcement date"]] = announce_year
    if zkp and "endorses/uses zkp" in hset:
        out[hset["endorses/uses zkp"]] = zkp
    if exportable and "has exportable credentials" in hset:
        out[hset["has exportable credentials"]] = exportable
    if key_storage and "credential and key storage" in hset:
        out[hset["credential and key storage"]] = key_storage
    if tgt_h and "targets holders" in hset:
        out[hset["targets holders"]] = tgt_h
    if tgt_i and "targets issuers" in hset:
        out[hset["targets issuers"]] = tgt_i
    if tgt_v and "targets verifiers" in hset:
        out[hset["targets verifiers"]] = tgt_v
    if tech_hits and "tech stack descriptions" in hset:
        v = ", ".join(sorted(set(tech_hits)))
        out[hset["tech stack descriptions"]] = v
        # pick the first evidence URL already added for tech keywords isn't simple; skip evidence here

    return out

def _write_text(project_name: str, filename: str, text: str):
    proj_dir = Path(__file__).resolve().parents[1] / "data" / "cache" / project_name.replace(" ", "_")
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / filename).write_text(text, encoding="utf-8")

# ---------- MAIN EXTRACTION ----------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def extract_record(project_name: str, headers: List[str], pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    print(f"[extract] {project_name}: pages received={len(pages)}")

    # --- Build compact context: first 2–3 page snippets (~1200 chars each)
    snippets = []
    for p in pages:
        t = (p.get("text") or "")
        u = (p.get("url") or "")
        if t:
            snippets.append(f"[URL]{u}\n{t[:1200]}")
        if len(snippets) >= 3:
            break

    name_h = _name_header(headers)
    print(f"[extract] {project_name}: using {len(snippets)} page snippets")

    # --- Seeds (minimal row from first page so we always have basics)
    seeds: Dict[str, Any] = {h: "" for h in headers}
    seeds[name_h] = project_name
    seeds["_evidence"] = []
    if pages:
        first = pages[0]
        url = (first.get("url") or "").strip()
        text = (first.get("text") or "").strip()
        # Website
        for h in headers:
            if h.lower() == "website" and url:
                seeds[h] = url
                seeds["_evidence"].append({
                    "field": "Website",
                    "value": url,
                    "source_url": url,
                    "source_type": "webpage",
                    "confidence": "medium",
                })
                break
        # Brief description if a column exists
        desc = (text[:240] + "…") if len(text) > 240 else text
        for h in headers:
            if h.lower() in ("description", "project description", "brief"):
                seeds[h] = desc
                if url:
                    seeds["_evidence"].append({
                        "field": h,
                        "value": desc,
                        "source_url": url,
                        "source_type": "webpage",
                        "confidence": "low",
                    })
                break

    # --- If no context, just return seeds (minimal)
    if not snippets:
        print(f"[extract] {project_name}: no context → returning seeds only")
        return seeds

    # --- Build LLM request
    context = "\n\n".join(snippets)
    user_payload = {
        "project": project_name,
        "headers": headers,
        "instructions": (
            "Set 'Product Name' exactly to the string provided in `project`.\n"
            "Output MUST be a single JSON object enclosed by <JSON> ... </JSON>.\n"
            "Use allowed enums for Status and ternary fields; use 4-digit years when obvious.\n"
            "Every non-empty field must include an evidence entry with a URL present in the context."
        ),
        "context": context,
    }
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": json.dumps(user_payload, ensure_ascii=False)},
    ]

    # Resolve provider/model/max_tokens (CLI may set env that overrides defaults)
    provider = os.getenv("LLM_PROVIDER", PROVIDER).lower().strip()
    model = os.getenv("LLM_MODEL", (OPENAI_MODEL if provider == "openai" else OLLAMA_MODEL)).strip()
    try:
        max_tokens = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", str(OPENAI_MAX_OUTPUT_TOKENS)))
    except Exception:
        max_tokens = OPENAI_MAX_OUTPUT_TOKENS

    print(f"[extract] {project_name}: calling LLM… (provider={provider})")
    try:
        raw = _chat_json(messages, provider=provider, model=model, max_tokens=max_tokens)
    except Exception as e:
        err_txt = f"{type(e).__name__}: {e}"
        print(f"[extract] {project_name}: LLM call failed → {err_txt}")
        _write_text(project_name, "llm_error.txt", err_txt)
        raw = ""

    # Persist raw for inspection
    _write_text(project_name, "llm_raw.json", raw if raw is not None else "")
    print(f"[extract] {project_name}: raw chars={len(raw or '')}")

    # --- Parse + coerce both LLM and seeds
    obj = parse_llm_json(raw or "")
    if not obj:
        print("[map] parse failed; using seeds only")
    else:
        print(f"[map] parse ok; keys={list(obj.keys())[:12]}")

    seeds_norm = _coerce_to_headers(seeds, headers, project_name=project_name)
    llm_norm   = _coerce_to_headers(obj or {}, headers, project_name=project_name)

    # --- Merge: keep seeds; fill empty seed fields with LLM values
    data: Dict[str, Any] = {h: seeds_norm.get(h, "") for h in headers}
    mapped = 0
    for h in headers:
        sv = seeds_norm.get(h, "")
        lv = llm_norm.get(h, "")
        if (isinstance(sv, str) and not sv.strip()) and (isinstance(lv, str) and lv.strip()):
            data[h] = lv
            mapped += 1
    print(f"[map] mapped={mapped} dropped=0")

    # Merge evidences
    ev: List[Dict[str, Any]] = []
    if isinstance(seeds_norm.get("_evidence"), list): ev += seeds_norm["_evidence"]
    if isinstance(llm_norm.get("_evidence"),   list): ev += llm_norm["_evidence"]
    data["_evidence"] = ev

    # --- Normalizations
    if "Status" in data:
        data["Status"] = normalize_status(data.get("Status"))
    for k in [
        "Endorses/Uses ZKP", "Has Exportable Credentials", "Credential and Key Storage",
        "Targets Holders", "Targets Issuers", "Targets Verifiers",
    ]:
        if k in data:
            data[k] = normalize_fd(data.get(k))
    for k in ["Announcement", "Launch", "Project Announcement Date", "Project Launch Date"]:
        if k in data:
            data[k] = normalize_year(data.get(k))

    # Ensure all headers present
    for h in headers:
        if h not in data:
            data[h] = ""

    # Ensure correct name column is set
    if not data.get(name_h):
        data[name_h] = project_name

    filled = sum(1 for k, v in data.items() if k != "_evidence" and isinstance(v, str) and v.strip())
    print(f"[extract] {project_name}: fields filled={filled}")
    return data
