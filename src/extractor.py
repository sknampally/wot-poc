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
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "1200"))

# --- Ollama config ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

SYSTEM = (
    "You are a meticulous research assistant for digital identity (SSI/DI) projects. "
    "Use ONLY the provided context. If a value is not present in the context, set it to 'Failed to disclose'. "
    "Return STRICT JSON keyed by the given headers. "
    "Include an `_evidence` array with objects: field, value, source_url, source_type, confidence. "
    "Use enums when obvious: Status=[Announced,Pilot,Launched,Discontinued]; "
    "Ternary fields use [True,False,Failed to disclose]; "
    "Year fields should be a 4-digit year when discoverable."
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
    # Using /api/chat (Ollama's chat endpoint)
    url = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 1024},
    }
    try:
        r = requests.post(url, json=payload, timeout=600)
        r.raise_for_status()
        j = r.json()
        # support both old/new schema keys
        return j.get("message", {}).get("content", "") or j.get("response", "")
    except requests.exceptions.ReadTimeout:
        # one retry after a short sleep
        import time
        time.sleep(5)
        r = requests.post(url, json=payload, timeout=600)
        r.raise_for_status()
        j = r.json()
        return j.get("message", {}).get("content", "") or j.get("response", "")

def _openai_chat_json(messages: List[Dict[str, str]]) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "temperature": OPENAI_TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": messages,
        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,
    }
    r = requests.post(url, json=payload, headers=headers, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _chat_json(messages: List[Dict[str, str]]) -> str:
    if PROVIDER == "openai":
        if not OPENAI_KEY:
            raise RuntimeError("OPENAI_API_KEY not set but LLM_PROVIDER=openai")
        return _openai_chat_json(messages)
    # default: local Ollama
    return _ollama_chat_json(messages)

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
    user_payload = {
        "project": project_name,
        "headers": headers,
        "instructions": (
            "Use allowed enums for Status and ternary fields; use 4-digit years when obvious. "
            "Every non-empty field must include an evidence entry with a URL present in the context."
        ),
        "context": context,
    }
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user_payload)},
    ]

    print(f"[extract] {project_name}: calling LLM… (provider={PROVIDER})")
    raw = _chat_json(messages)
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

    filled = sum(1 for k, v in data.items() if k != "_evidence" and isinstance(v, str) and v.strip())
    print(f"[extract] {project_name}: fields filled={filled}")
    return data
