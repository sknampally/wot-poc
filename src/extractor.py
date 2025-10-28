import re
from pathlib import Path
import os, json, requests
from typing import Dict, Any, List
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from schema import normalize_status, normalize_fd, normalize_year, _name_header

# Load .env
load_dotenv()

# Provider selection via env (overridable by CLI through os.environ in main.py)
PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

# --- OpenAI config ---
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "600")))
FALLBACK_TO_OPENAI_ON_OLLAMA_FAIL = os.getenv("FALLBACK_TO_OPENAI_ON_OLLAMA_FAIL", "false").lower() in ("1","true","yes")

# --- Ollama config ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

SYSTEM = (
    "You are an extraction agent for digital identity (SSI/DI) projects.\n"
    "Return a SINGLE JSON object ONLY (no prose, no markdown). Keys MUST match the provided headers.\n"
    "Rules:\n"
    "• Use ONLY facts in the provided context.\n"
    "• If a field is present in context, fill it; else set it exactly to 'Failed to disclose'.\n"
    "• Never invent values.\n"
    "• For every non-empty field, add an object to `_evidence` with fields:\n"
    "  {\"field\":\"<header>\", \"value\":\"<copied value>\", \"source_url\":\"<URL from context>\", \"source_type\":\"webpage\", \"confidence\":\"low|medium|high\"}\n"
    "• Status ∈ [Announced,Pilot,Launched,Discontinued].\n"
    "• Ternary fields ∈ [True,False,Failed to disclose].\n"
    "• Year fields must be 4-digit when present.\n"
)

# minimal synonym map → target header (normalized)
_SYNONYMS = {
    "project": "name", "project name": "name", "product": "name",
    "status": "status",
    "website": "website", "url": "website", "homepage": "website", "official site": "website",
    "description": "description", "summary": "description", "about": "description",
    "announcement year": "announcement", "announced": "announcement",
    "launch year": "launch", "launched": "launch",
    "targets holders": "targets holders",
    "targets issuers": "targets issuers",
    "targets verifiers": "targets verifiers",
    "endorses uses zkp": "endorses/uses zkp", "zkp": "endorses/uses zkp",
    "has exportable credentials": "has exportable credentials",
    "credential and key storage": "credential and key storage",
}

def _salvage_to_json_obj(raw: str):
    """Best-effort salvage: slice outermost {...}, remove trailing commas before } and parse."""
    if not isinstance(raw, str):
        return {}
    s = raw.strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return {}
    frag = s[i:j+1]
    # remove trailing commas like `, }` or `,}` which break strict JSON
    frag = re.sub(r",\s*}", "}", frag)
    try:
        return json.loads(frag)
    except Exception:
        return {}
    
def _json_load_relaxed(raw: str):
    """
    Robustly turn model output into a Python object.
    Handles: normal JSON, JSON wrapped in a string, codefence wrappers, etc.
    Returns dict/list or {} if it can't parse.
    """
    if not isinstance(raw, str):
        return {}
    s = raw.strip()

    # strip ```json ... ``` fences if present
    if s.startswith("```"):
        # keep everything between the first and last ```
        parts = s.split("```")
        if len(parts) >= 3:
            # commonly: ["", "json", "{...}", ""]
            s = parts[2].strip() if parts[1].lower().strip() in ("json", "json5") else parts[1].strip()

    # 1st load
    try:
        obj = json.loads(s)
    except Exception:
        # try to salvage by slicing between first { and last }
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            frag = s[start:end+1]
            try:
                obj = json.loads(frag)
            except Exception:
                return {}
        else:
            return {}

    # If the result is a string that itself looks like JSON, load again (double-encoded case)
    if isinstance(obj, str):
        t = obj.strip()
        if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
            try:
                obj = json.loads(t)
            except Exception:
                pass

    # Only accept dict/list
    if not isinstance(obj, (dict, list)):
        return {}
    return obj

def _write_debug(project_name: str, name: str, payload: dict | list | str):
    """Write small debug artifacts per project to help diagnose."""
    try:
        pdir = Path("data/cache") / project_name.replace(" ", "_")
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) if not isinstance(payload, str) else str(payload),
            encoding="utf-8",
        )
    except Exception:
        pass

def _norm_k(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\s_\-]+", " ", s)      # unify separators
    s = re.sub(r"[^a-z0-9 ]", "", s)     # drop punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _coerce_to_headers(obj, headers: list[str], project_name: str | None = None) -> dict:
    """
    Map model JSON to your exact Excel headers.
    Writes a per-project debug map: data/cache/<Project>/debug_map.json
    """
    # unwrap wrappers
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], dict):
        obj = obj["data"]
    if not isinstance(obj, dict):
        return {}

    # build normalized header index
    norm_to_header = { _norm_k(h): h for h in headers }

    # figure out your "name" column (e.g., "Product Name")
    try:
        from schema import _name_header
        name_col = _name_header(headers)
    except Exception:
        name_col = None

    # synonyms (only if target header exists)
    synonyms = {
        "url": "website",
        "homepage": "website",
        "official site": "website",
        "project announcement date": "announcement",
        "announcement date": "announcement",
        "announced": "announcement",
        "launch date": "launch",
        "launched": "launch",
    }
    # attach name-like aliases if we know the name column
    if name_col:
        for k in ("product name", "project name", "name"):
            synonyms[k] = name_col

    for syn, target in list(synonyms.items()):
        tnorm = _norm_k(target)
        if tnorm in norm_to_header:
            norm_to_header[_norm_k(syn)] = norm_to_header[tnorm]

    out = {h: "" for h in headers}
    mapped, dropped = {}, []

    # special mapper for “… Source” variants
    def map_source_key(norm_key: str) -> str | None:
        # try "<base> source" style
        m = re.match(r"(?:live|archived)?\s*source\s+(.+)", norm_key) or re.match(r"(.+)\s+source$", norm_key)
        if m:
            base = m.group(1).strip()
            candidate = f"{base} source"
            for cand_norm, hdr in norm_to_header.items():
                if cand_norm == candidate:
                    return hdr
        return None

    for k, v in obj.items():
        nk = _norm_k(str(k))

        # 1) direct/synonym
        if nk in norm_to_header:
            out[norm_to_header[nk]] = v
            mapped[k] = norm_to_header[nk]
            continue

        # 2) “… Source” style
        hsrc = map_source_key(nk)
        if hsrc:
            out[hsrc] = v
            mapped[k] = hsrc
            continue

        # 3) loose contains
        hit = None
        for cand_norm, hdr in norm_to_header.items():
            if nk == cand_norm or nk in cand_norm or cand_norm in nk:
                hit = hdr; break
        if hit:
            out[hit] = v
            mapped[k] = hit
        else:
            dropped.append(k)

    # write per-project debug map next to llm_raw.json
    try:
        if project_name:
            proj_dir = Path("data/cache") / project_name.replace(" ", "_")
        else:
            proj_dir = Path("data/cache")
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "debug_map.json").write_text(
            json.dumps({"mapped": mapped, "dropped": dropped}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[map] warn: failed to write debug_map.json :: {e}")

    print(f"[map] mapped={len(mapped)} dropped={len(dropped)}")
    return out

def _repair_json(s: str) -> str:
    try:
        json.loads(s)
        return s
    except Exception:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            json.loads(s[start:end+1])
            return s[start:end+1]
        except Exception:
            pass
    return "{}"

def _ollama_chat_json(messages: List[Dict[str, str]]) -> str:
    """
    Call Ollama chat with strict timeouts & retries.
    We DO NOT pass format='json' (many models ignore it). We enforce JSON via the prompt instead.
    """
    url = f"{OLLAMA_HOST}/api/chat"

    def make_payload(msgs: List[Dict[str, str]], num_predict: int, num_ctx: int) -> dict:
        return {
            "model": OLLAMA_MODEL,
            "messages": msgs,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }

    attempts = [
        {"num_predict": 450, "num_ctx": 2048, "timeout": 90},
        {"num_predict": 280, "num_ctx": 1536, "timeout": 75},
        {"num_predict": 180, "num_ctx": 1024, "timeout": 60},
    ]

    msgs = [dict(m) for m in messages]

    for i, cfg in enumerate(attempts, start=1):
        payload = make_payload(msgs, cfg["num_predict"], cfg["num_ctx"])
        try:
            print(f"[ollama] attempt {i}/3 → ctx={cfg['num_ctx']} predict={cfg['num_predict']} timeout={cfg['timeout']}s")
            r = requests.post(url, json=payload, timeout=cfg["timeout"])
            r.raise_for_status()
            j = r.json()
            content = j.get("message", {}).get("content", "") or j.get("response", "")
            if content and content.strip():
                return content
            print(f"[ollama] empty content on attempt {i}, trimming prompt and retrying…")
        except requests.exceptions.ReadTimeout:
            print(f"[ollama] ReadTimeout on attempt {i} → trimming and retrying…")
        except Exception as e:
            body = ""
            try:
                body = r.text[:800]  # type: ignore
            except Exception:
                pass
            raise RuntimeError(f"Ollama error: {e}; body={body}") from e

        # Trim last user message to keep instructions, cut context if needed
        for m in reversed(msgs):
            if m.get("role") == "user":
                s = m.get("content", "")
                if len(s) > 1400:
                    m["content"] = s[-1200:]
                    break

    # All attempts failed → return empty string (caller will handle)
    return ""

def _ollama_generate_json(messages: List[Dict[str, str]]) -> str:
    """
    Fallback to /api/generate: flatten messages into one prompt and ask for JSON.
    """
    url = f"{OLLAMA_HOST}/api/generate"

    # flatten messages into one prompt (system first, then user)
    sys_txt, user_txt = "", ""
    for m in messages:
        role = m.get("role")
        if role == "system":
            sys_txt += (m.get("content") or "") + "\n\n"
        elif role == "user":
            user_txt += (m.get("content") or "") + "\n\n"

    prompt = (
        sys_txt.strip() + "\n\n"
        "Return a SINGLE JSON object only (no prose).\n\n"
        "USER:\n" + user_txt.strip()
    )

    attempts = [
        {"num_predict": 450, "timeout": 90},
        {"num_predict": 280, "timeout": 75},
        {"num_predict": 180, "timeout": 60},
    ]
    for i, cfg in enumerate(attempts, start=1):
        try:
            print(f"[ollama-generate] attempt {i}/3 → predict={cfg['num_predict']} timeout={cfg['timeout']}s")
            r = requests.post(url, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": cfg["num_predict"]},
            }, timeout=cfg["timeout"])
            r.raise_for_status()
            j = r.json()
            content = j.get("response", "") or j.get("message", {}).get("content", "")
            if content and content.strip():
                return content
        except requests.exceptions.ReadTimeout:
            print(f"[ollama-generate] ReadTimeout on attempt {i}")
            continue
        except Exception as e:
            body = ""
            try: body = r.text[:800]  # type: ignore
            except: pass
            raise RuntimeError(f"Ollama /generate error: {e}; body={body}") from e
    return ""

def _openai_chat_json(messages: List[Dict[str, str]]) -> str:
    import time, random
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENAI_MODEL,
        "temperature": OPENAI_TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": messages,
        "max_tokens": OPENAI_MAX_TOKENS,
    }
    for attempt in range(1, 6):
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code == 200:
            j = r.json()
            return j["choices"][0]["message"]["content"]
        # print body for debugging
        body = r.text[:1000]
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            sleep_s = float(ra) if ra and ra.isdigit() else (2 * attempt) + random.uniform(0.2, 0.8)
            print(f"[openai] 429, sleeping {sleep_s:.1f}s (attempt {attempt}/5) :: {body}")
            time.sleep(sleep_s)
            continue
        print(f"[openai] HTTP {r.status_code} :: {body}")
        r.raise_for_status()
    raise requests.HTTPError("OpenAI 429 persisted after retries")

def _chat_json(messages: List[Dict[str, str]]) -> str:
    if PROVIDER == "openai":
        if not OPENAI_KEY:
            raise RuntimeError("OPENAI_API_KEY not set but LLM_PROVIDER=openai")
        return _openai_chat_json(messages)

    # default: local Ollama
    out = _ollama_chat_json(messages)
    if not out.strip():
        print("[extract] Ollama chat returned empty → trying /api/generate fallback")
        out = _ollama_generate_json(messages)

    if not out.strip() and FALLBACK_TO_OPENAI_ON_OLLAMA_FAIL and OPENAI_KEY:
        print("[extract] Ollama still empty → falling back to OpenAI (using OPENAI_MODEL)")
        return _openai_chat_json(messages)

    return out

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def extract_record(project_name: str, headers: List[str], pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Build compact context (first 3 pages, short snippets) for reliability & token control
    print(f"[extract] {project_name}: pages received={len(pages)}")

    snippets = []
    for p in pages:
        t = p.get("text", ""); u = p.get("url", "")
        if t:
            # limit each page snippet to ~1200 chars to keep total prompt tight
            snippets.append(f"[URL]{u}\n{t[:1200]}")
        if len(snippets) >= 3:
            break

    name_h = _name_header(headers)
    print(f"[extract] {project_name}: using {len(snippets)} page snippets")
    if not snippets:
        # No context: return a minimal row so export can proceed safely
        data = {h: "" for h in headers}
        data[name_h] = project_name
        data["_evidence"] = []
        print(f"[extract] {project_name}: no context → minimal row only")
        return data

    context = "\n\n".join(snippets)
    example = {
        "Status": "Launched",
        "Targets Holders": "True",
        "Announcement": "2021",
        "Website": "https://example.com"
    }

    user_payload = {
        "project": project_name,
        "headers": headers,
        "instructions": (
            "Use allowed enums for Status and ternary fields; use 4-digit years when obvious. "
            "Every non-empty field must include an evidence entry with a URL present in the context."
        ),
        "example": example,
        "context": context
    }

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user_payload)},
    ]

    print(f"[extract] {project_name}: calling LLM… (provider={PROVIDER})")
    raw = _chat_json(messages)
    try:
        from pathlib import Path as _Path
        _proj_dir = _Path(__file__).resolve().parents[1] / "data" / "cache" / project_name.replace(" ", "_")
        _proj_dir.mkdir(parents=True, exist_ok=True)
        (_proj_dir / "llm_raw.json").write_text(raw if raw is not None else "", encoding="utf-8")
        print(f"[extract] {project_name}: raw chars={len(raw or '')}")
    except Exception:
        pass

    # If model returned nothing, fall back to a minimal skeleton so pipeline completes
    if not raw or not raw.strip():
        data = {h: "" for h in headers}
        data[_name_header(headers)] = project_name
        data["_evidence"] = []
        return data

    # 1) try strict JSON first
    parsed = _json_load_relaxed(raw or "")

    # 2) if that failed, try salvage
    if not parsed:
        parsed = _salvage_to_json_obj(raw or "")
        if parsed:
            print("[map] salvage parser succeeded")
        else:
            print("[map] parse produced empty object; writing debug_parsed.json")
            _write_debug(project_name, "debug_parsed.json", raw or "")

    # 3) proceed to coercion
    data = _coerce_to_headers(parsed, headers, project_name=project_name)

    # 4) visibility
    if isinstance(parsed, dict):
        print(f"[map] parsed_keys={list(parsed.keys())[:10]}")
    elif isinstance(parsed, list):
        print(f"[map] parsed_list_len={len(parsed)} headType={(type(parsed[0]).__name__ if parsed else 'n/a')}")
    else:
        print(f"[map] parsed_type={type(parsed).__name__}")

    # Normalizations
    if "Status" in data:
        data["Status"] = normalize_status(data.get("Status"))
    for k in [
        "Endorses/Uses ZKP", "Has Exportable Credentials", "Credential and Key Storage",
        "Targets Holders", "Targets Issuers", "Targets Verifiers"
    ]:
        if k in data:
            data[k] = normalize_fd(data.get(k))
    for k in ["Announcement", "Launch"]:
        if k in data:
            data[k] = normalize_year(data.get(k))

    # Evidence guard
    ev = data.get("_evidence", [])
    if not isinstance(ev, list):
        ev = []
    data["_evidence"] = ev

    # Ensure all headers present
    for h in headers:
        if h not in data:
            data[h] = ""

    # Ensure correct name column is set
    if not data.get(name_h):
        data[name_h] = project_name

    # Optional: map first evidence URLs into "<Field> Source" columns when present
    if ev:
        headers_lower = {h.lower(): h for h in headers}
        for e in ev:
            f = (e.get("field") or "").strip()
            src = (e.get("source_url") or "").strip()
            if not f or not src:
                continue
            src_col = f"{f} Source"
            if src_col.lower() in headers_lower:
                data[headers_lower[src_col.lower()]] = src

    # If essentially nothing was extracted, fill a couple of basics from pages as a safety net
    non_meta = [k for k in data.keys() if k not in ("_evidence",)]
    non_empty = any(isinstance(data.get(k), str) and data.get(k).strip() for k in non_meta)
    if not non_empty:
        # try to set Website and Description from first page
        if pages:
            data[_name_header(headers)] = project_name
            first = pages[0]
            url = (first.get("url") or "").strip()
            text = (first.get("text") or "").strip()
            if "website" in (h.lower() for h in headers) and url:
                # find exact header case
                for h in headers:
                    if h.lower() == "website":
                        data[h] = url
                        break
            # crude description: first ~240 chars
            desc = (text[:240] + "…") if len(text) > 240 else text
            for h in headers:
                if h.lower() in ("description","project description","brief"):
                    data[h] = desc
                    break
            data["_evidence"] = data.get("_evidence", [])
            if url:
                data["_evidence"].append({
                    "field": "Website",
                    "value": url,
                    "source_url": url,
                    "source_type": "webpage",
                    "confidence": "medium"
                })

    filled = sum(1 for k, v in data.items() if k != "_evidence" and isinstance(v, str) and v.strip())
    print(f"[extract] {project_name}: fields filled={filled}")
    return data
