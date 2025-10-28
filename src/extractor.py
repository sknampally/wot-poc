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

    raw = _repair_json(raw)
    data = json.loads(raw or "{}")

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
