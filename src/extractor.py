# src/extractor.py
# Hardened extractor: robust JSON parsing + header coercion + dual provider (Ollama / OpenAI)

import os, re, json, requests, time, unicodedata
from typing import Dict, Any, List
from pathlib import Path
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from schema import normalize_status, normalize_fd, normalize_year, _name_header

# ---------- ENV / CONFIG ----------
load_dotenv()

# Provider can be overridden at runtime by main.py via os.environ["LLM_PROVIDER"]
PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower().strip()

# OpenAI
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
# main.py also passes --max-output-tokens which sets this env at runtime
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "600")))

# Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# ---------- SYSTEM PROMPT ----------
SYSTEM = (
    "You are an extraction agent for digital identity (SSI/DI) projects.\n"
    "Return ONE valid JSON object only. No markdown. No prose. No code fences.\n"
    "Keys MUST match the provided headers when applicable.\n"
    "Rules:\n"
    "• Use ONLY facts in the provided context (which includes quoted page snippets).\n"
    "• If a field is not present in context, set exactly: 'Failed to disclose'.\n"
    "• Never invent values.\n"
    "• Every non-empty field must have an evidence item in `_evidence`:\n"
    "  {\"field\":\"<header>\", \"value\":\"<copied value>\", \"source_url\":\"<URL from context>\", \"source_type\":\"webpage\", \"confidence\":\"low|medium|high\"}\n"
    "• Status ∈ [Announced, Pilot, Launched, Discontinued].\n"
    "• Ternary fields ∈ [True, False, Failed to disclose].\n"
    "• Year fields must be 4-digit when present.\n"
    "Ensure the JSON ends with a closing brace '}'. No trailing commas."
)

# ---------- UTILS: Debug Writers ----------
_SMART_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
    "\u201C": '"', "\u201D": '"', "\u201E": '"',
}

def _normalize_quotes(s: str) -> str:
    if not s: return s
    s = "".join(_SMART_QUOTES.get(ch, ch) for ch in s)
    return s

_pair_re = re.compile(r'"([^"\n\r]{1,200})"\s*:\s*"([^"]{0,5000})"')

def _kv_pairs_from_text(raw: str) -> dict:
    """
    Ultra-relaxed extractor: normalize quotes, then regex out "key":"value" pairs.
    Works even if the blob isn't valid JSON (trailing commas, missing braces, etc.).
    """
    if not raw: return {}
    raw = _normalize_quotes(raw)
    # Try to bracket if the model omitted the outer braces
    if "{" not in raw and "}" not in raw:
        raw = "{ " + raw + " }"
    # Remove obviously broken comma-before-brace patterns:
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    # Extract pairs
    out = {}
    for m in _pair_re.finditer(raw):
        k = m.group(1).strip()
        v = m.group(2).strip()
        # Skip obvious non-keys
        if not k or len(k) > 200: 
            continue
        out[k] = v
    return out

def _proj_dir_for(name: str) -> Path:
    p = Path("data/cache") / name.replace(" ", "_")
    p.mkdir(parents=True, exist_ok=True)
    return p

def _write_text(project_name: str, filename: str, content: str):
    try:
        p = _proj_dir_for(project_name) / filename
        p.write_text(content, encoding="utf-8")
    except Exception:
        pass

def _write_json(project_name: str, filename: str, payload: Any):
    try:
        p = _proj_dir_for(project_name) / filename
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

# ---------- UTILS: Key Normalization + Header Coercion ----------
def _norm_k(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\s_\-]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _coerce_to_headers(obj, headers: List[str], project_name: str | None = None) -> dict:
    """
    Map model JSON (dict or list[dict]) into a dict keyed by your exact headers.
    Writes per-project 'debug_map.json'.
    """
    # unwrap wrappers
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], dict):
        obj = obj["data"]
    if not isinstance(obj, dict):
        _write_json(project_name or "unknown", "debug_map.json", {"mapped": {}, "dropped": ["<not a dict>"]})
        return {h: "" for h in headers}

    norm_to_hdr = { _norm_k(h): h for h in headers }

    # find the official name column from the spreadsheet
    try:
        name_col = _name_header(headers)
    except Exception:
        name_col = None

    # common synonyms → only map if target header exists
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
    if name_col:
        for k in ("product name","project name","name"):
            synonyms[k] = name_col

    for syn, target in list(synonyms.items()):
        tn = _norm_k(target)
        if tn in norm_to_hdr:
            norm_to_hdr[_norm_k(syn)] = norm_to_hdr[tn]

    out = {h: "" for h in headers}
    mapped, dropped = {}, []

    def map_source_key(norm_key: str) -> str | None:
        # matches "... source" or "live source X", "archived source X"
        m = re.match(r"(?:live|archived)?\s*source\s+(.+)", norm_key) or re.match(r"(.+)\s+source$", norm_key)
        if m:
            base = m.group(1).strip()
            candidate = f"{base} source"
            for cand_norm, hdr in norm_to_hdr.items():
                if cand_norm == candidate:
                    return hdr
        return None

    for k, v in obj.items():
        nk = _norm_k(str(k))
        if nk in norm_to_hdr:
            out[norm_to_hdr[nk]] = v
            mapped[k] = norm_to_hdr[nk]
            continue
        hsrc = map_source_key(nk)
        if hsrc:
            out[hsrc] = v
            mapped[k] = hsrc
            continue
        # loose contains
        hit = None
        for cand_norm, hdr in norm_to_hdr.items():
            if nk == cand_norm or nk in cand_norm or cand_norm in nk:
                hit = hdr; break
        if hit:
            out[hit] = v
            mapped[k] = hit
        else:
            dropped.append(k)

    _write_json(project_name or "unknown", "debug_map.json", {"mapped": mapped, "dropped": dropped})
    print(f"[map] mapped={len(mapped)} dropped={len(dropped)}")
    return out

# ---------- UTILS: Hardened JSON Parsing ----------
def _strip_code_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 3:
            body = parts[2] if parts[1].strip().lower() in ("json","json5") else parts[1]
            return body.strip()
    return s

def _remove_trailing_commas(s: str) -> str:
    return re.sub(r",\s*}", "}", s)

def _double_decode_if_string(obj):
    if isinstance(obj, str):
        t = obj.strip()
        if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
            try:
                return json.loads(t)
            except Exception:
                return obj
    return obj

def _longest_balanced_json_obj(s: str) -> str | None:
    idxs, stack = [], []
    for i,ch in enumerate(s):
        if ch == "{":
            stack.append(i)
        elif ch == "}":
            if stack:
                start = stack.pop()
                idxs.append((start, i+1))
    idxs.sort(key=lambda t: t[1]-t[0], reverse=True)
    for a,b in idxs:
        frag = _remove_trailing_commas(s[a:b])
        try:
            json.loads(frag); return frag
        except Exception:
            continue
    return None

def _fix_missing_closing_brace(s: str) -> str | None:
    s2 = s.strip()
    if not s2.startswith("{"):
        return None
    # If already closed, do nothing
    if "}" in s2 and s2.rfind("}") > s2.find("{"):
        return None
    # drop trailing comma then append one closing brace
    s2 = re.sub(r",\s*$", "", s2)
    return s2 + "}"

def parse_llm_json(raw: str):
    """
    Try multiple strategies to convert LLM output into a dict/list.
    Returns (obj, strategy).
    """
    if not isinstance(raw, str) or not raw.strip():
        return {}, "empty"

    s = _strip_code_fences(raw)

    # 1) direct
    try:
        obj = json.loads(s)
        obj = _double_decode_if_string(obj)
        if isinstance(obj, (dict,list)):
            return obj, "direct"
    except Exception:
        pass

    # 2) fix missing closing brace
    fixed = _fix_missing_closing_brace(s)
    if fixed is not None:
        try:
            obj = json.loads(fixed)
            obj = _double_decode_if_string(obj)
            if isinstance(obj, (dict,list)):
                return obj, "missing-brace-fix"
        except Exception:
            pass

    # 3) outer slice
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        frag = _remove_trailing_commas(s[i:j+1])
        try:
            obj = json.loads(frag)
            obj = _double_decode_if_string(obj)
            if isinstance(obj, (dict,list)):
                return obj, "outer"
        except Exception:
            pass

    # 4) longest balanced
    frag = _longest_balanced_json_obj(s)
    if frag:
        try:
            obj = json.loads(frag)
            obj = _double_decode_if_string(obj)
            if isinstance(obj, (dict,list)):
                return obj, "balanced"
        except Exception:
            pass

    # 5) quote-fix (last resort)
    ss = re.sub(r"'", '"', s)
    try:
        obj = json.loads(ss)
        obj = _double_decode_if_string(obj)
        if isinstance(obj, (dict,list)):
            return obj, "quote-fix"
    except Exception:
        pass

    return {}, "failed"

# ---------- LLM CALLS ----------
def _ollama_chat_json(messages: List[Dict[str, str]], model_override: str | None = None) -> str:
    """
    Call Ollama /api/chat with backoff + shrinking prompt; if still empty, try /api/generate.
    Returns raw string content.
    """
    model = (model_override or OLLAMA_MODEL).strip()
    url_chat = f"{OLLAMA_HOST}/api/chat"
    url_gen  = f"{OLLAMA_HOST}/api/generate"

    def make_chat_payload(msgs, num_predict, num_ctx):
        return {
            "model": model,
            "messages": msgs,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }

    msgs = [dict(m) for m in messages]
    attempts = [
        {"num_predict": 450, "num_ctx": 2048, "timeout": 90},
        {"num_predict": 280, "num_ctx": 1536, "timeout": 75},
        {"num_predict": 180, "num_ctx": 1024, "timeout": 60},
    ]

    for i, cfg in enumerate(attempts, 1):
        try:
            print(f"[ollama] attempt {i}/3 → ctx={cfg['num_ctx']} predict={cfg['num_predict']} timeout={cfg['timeout']}s")
            r = requests.post(url_chat, json=make_chat_payload(msgs, cfg["num_predict"], cfg["num_ctx"]), timeout=cfg["timeout"])
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
            try: body = r.text[:800]  # type: ignore
            except Exception: pass
            raise RuntimeError(f"Ollama error: {e}; body={body}") from e

        # Trim the last user message content to reduce prompt
        for m in reversed(msgs):
            if m.get("role") == "user":
                s = m.get("content", "")
                if len(s) > 1400:
                    m["content"] = s[-1200:]
                    break

    # Fallback: /api/generate (system+user merged)
    user_text = "\n\n".join([m["content"] for m in msgs if m.get("role") == "user"])
    gen_payload = {
        "model": model,
        "prompt": user_text,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 300},
    }
    for i in range(1, 4):
        try:
            print(f"[ollama-generate] attempt {i}/3 → predict={gen_payload['options']['num_predict']} timeout=90s")
            r = requests.post(url_gen, json=gen_payload, timeout=90)
            r.raise_for_status()
            j = r.json()
            content = j.get("response", "")
            if content and content.strip():
                return content
        except requests.exceptions.ReadTimeout:
            print(f"[ollama-generate] ReadTimeout on attempt {i}")
        except Exception:
            pass

    return ""  # model gave nothing

def _openai_chat_json(messages: List[Dict[str, str]], model_override: str | None = None) -> str:
    """
    Call OpenAI Chat Completions with JSON mode.
    NOTE: Chat Completions expects `max_tokens` (not `max_output_tokens`).
    """
    model = (model_override or OPENAI_MODEL).strip()
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": OPENAI_TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": messages,
        # IMPORTANT: use max_tokens here
        "max_tokens": OPENAI_MAX_TOKENS,
    }

    last_body = ""
    for attempt in range(1, 6):
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        if r.status_code == 200:
            try:
                j = r.json()
                return j["choices"][0]["message"]["content"]
            except Exception:
                # if parsing somehow fails, return raw text to be salvaged later
                return r.text

        # Handle transient / quota-ish errors with backoff
        if r.status_code in (429, 500, 502, 503, 504):
            try:
                last_body = r.text[:600]
            except Exception:
                last_body = "<no body>"
            sleep = min(10.0 * attempt, 30.0)
            print(f"[openai] {r.status_code}, sleeping {sleep:.1f}s (attempt {attempt}/5) :: {last_body}")
            time.sleep(sleep)
            continue

        # 400/other hard errors → don't crash the pipeline; return "{}" so we keep going
        try:
            last_body = r.text[:600]
        except Exception:
            last_body = "<no body>"
        print(f"[openai] HTTP {r.status_code} :: {last_body}")
        return "{}"

    # Out of retries; best-effort return "{}" so downstream continues
    print(f"[openai] final failure after retries :: {last_body}")
    return "{}"

def _chat_json(messages: List[Dict[str, str]], provider: str, model: str) -> str:
    if provider == "openai":
        if not OPENAI_KEY:
            raise RuntimeError("OPENAI_API_KEY not set but LLM_PROVIDER=openai")
        return _openai_chat_json(messages, model_override=model)
    # default: ollama
    return _ollama_chat_json(messages, model_override=model)

# ---------- MAIN EXTRACTION ----------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def extract_record(project_name: str, headers: List[str], pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Two-pass, field-aware extractor:
      Pass A (profile): Product Name, Website, Mission, Status, Announcement/Launch years
      Pass B (tech/targets/booleans): ZK, storage, targets, etc.
    Each pass builds a compact, field-aware context from the most relevant pages.
    """
    import re
    print(f"[extract] {project_name}: pages received={len(pages)}")

    # ---------- helpers ----------
    def _infer_domain(url: str) -> str:
        try:
            m = re.search(r"https?://(?:www\.)?([^/]+)/?", url or "")
            return (m.group(1) or "").lower() if m else ""
        except Exception:
            return ""

    def _score(text: str, kws: List[str]) -> int:
        if not text: return 0
        t = text.lower()
        sc = 0
        for w in kws:
            w = w.lower()
            if w in t: sc += 3
            sc += t.count(w)
        return sc

    def _pick_pages_for(field_kws: List[str], k: int = 2, prefer_domain: str = "") -> List[Dict[str, Any]]:
        # score each page for given keywords + small boost if on main domain
        scored = []
        for p in pages:
            txt = (p.get("text") or "")
            if not txt.strip(): continue
            u = (p.get("url") or "")
            s = _score(txt, field_kws)
            if prefer_domain and prefer_domain in (u.lower()):
                s += 4
            if s > 0:
                scored.append((s, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:k]]

    def _snip(p: Dict[str, Any], limit: int = 1200) -> str:
        return f"[URL]{p.get('url','')}\n{(p.get('text') or '')[:limit]}"

    def _ensure_name_alias(data: Dict[str, Any], parsed: Dict[str, Any]):
        name_h = _name_header(headers)
        detected = (parsed.get("Product Name") or "").strip() if isinstance(parsed, dict) else ""
        if data.get(name_h, "").strip().lower() != project_name.strip().lower():
            alias = detected or data.get(name_h, "")
            # append alias into a free-text column
            for cand in ["Tech Stack Descriptions", "Project Description", "Description"]:
                for h in headers:
                    if h.lower() == cand.lower():
                        prev = (data.get(h) or "").strip()
                        data[h] = (prev + (" | " if prev else "") + f"Alias: {alias}").strip()
                        break
            data[name_h] = project_name
        else:
            data[name_h] = project_name

    def _set_source_for_field(data: Dict[str, Any], field: str, url: str):
        if not field or not url: return
        lut = {h.lower(): h for h in headers}
        candidates = [f"{field} Source", f"Live Source {field}", f"Source {field}"]
        for c in candidates:
            lc = c.lower()
            if lc in lut:
                data[lut[lc]] = url
                return

    # ---------- domain preference ----------
    main_domain = ""
    if pages:
        main_domain = _infer_domain(pages[0].get("url", ""))
    if main_domain:
        print(f"[extract] {project_name}: inferred main domain → {main_domain}")

    # ---------- field keyword map ----------
    # Lightweight heuristics so we feed the model the *right* pages.
    FIELD_KWS_A = {
        "Product Name": ["brand", "product", "solution", "platform", "our product", "about"],
        "Website": ["http", "https", project_name.lower()],
        "Mission Statement": ["mission", "our mission", "we aim", "we are building", "vision", "about"],
        "Status": ["status", "launched", "pilot", "announced", "discontinued", "mainnet", "beta", "production"],
        "Project Announcement Date": ["announce", "announcement", "announced", "launch", "mainnet", "beta", "2020", "2021", "2022", "2023", "2024", "2025"],
        "Project Launch Date": ["launch", "launched", "mainnet", "go live", "went live", "production", "2020", "2021", "2022", "2023", "2024", "2025"],
    }
    FIELD_KWS_B = {
        "Endorses/Uses ZKP": ["zero-knowledge", "zkp", "zero knowledge", "zk"],
        "Has Exportable Credentials": ["export", "interoperable", "portable", "download", "export credential"],
        "Credential and Key Storage": ["wallet", "key", "storage", "custody", "custodial", "non-custodial", "cloud", "device"],
        "Targets Holders": ["holder", "user", "citizen", "customer"],
        "Targets Issuers": ["issuer", "issue credential", "issuing authority", "government"],
        "Targets Verifiers": ["verifier", "verify", "validation", "check"],
        "Tech Stack Descriptions": ["verifiable credential", "did", "ssi", "eidas", "ledger", "blockchain", "hyperledger", "w3c", "cheqd", "ethereum", "trust registry"],
    }

    # ---------- Pass A: profile ----------
    # choose up to 3 best pages total for profile fields
    selected_A: List[Dict[str, Any]] = []
    for f, kws in FIELD_KWS_A.items():
        selected_A.extend(_pick_pages_for(kws, k=1, prefer_domain=main_domain))
    # de-dup while preserving order
    seen = set(); selected_A = [p for p in selected_A if (p.get("url") or "") not in seen and not seen.add(p.get("url") or "")]
    # keep it tight (OpenAI struggles with too much)
    provider = os.getenv("LLM_PROVIDER", PROVIDER).lower().strip()
    max_pages_A = 3 if provider != "openai" else 2
    selected_A = selected_A[:max_pages_A]

    context_A = "\n\n".join(_snip(p) for p in selected_A) if selected_A else ""
    if not context_A:
        # fallback to first couple non-empty pages
        fallback = [p for p in pages if (p.get("text") or "").strip()][:2]
        context_A = "\n\n".join(_snip(p) for p in fallback)

    profile_headers = [h for h in headers if h in [
        "Product Name","Website","Mission Statement","Status",
        "Project Announcement Date","Project Launch Date"
    ]]
    name_h = _name_header(headers)
    if name_h not in profile_headers:
        profile_headers = [name_h] + profile_headers

    user_payload_A = {
        "project": project_name,
        "headers": profile_headers,
        "instructions": (
            "Return ONE JSON object only. Keys must match headers exactly. "
            "Use only the provided context. If truly not present, set 'Failed to disclose'. "
            "The 'Product Name' MUST equal the provided 'project' verbatim. "
            "For every non-empty field, add an _evidence item with {field,value,source_url,source_type,confidence}."
        ),
        "context": context_A,
    }
    messages_A = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user_payload_A, ensure_ascii=False)},
    ]
    print(f"[extract] {project_name}: calling LLM (Pass A)…")
    try:
        raw_A = _chat_json(messages_A, provider=os.getenv("LLM_PROVIDER", PROVIDER).lower().strip(),
                           model=os.getenv("LLM_MODEL", OLLAMA_MODEL if provider != "openai" else OPENAI_MODEL).strip())
    except Exception as e:
        raw_A = ""
        _write_text(project_name, "llm_error_A.txt", f"{type(e).__name__}: {e}")
    _write_text(project_name, "llm_raw_A.json", raw_A or "")

    parsed_A = {}
    if raw_A.strip():
        try:
            parsed_A = json.loads(raw_A)
        except Exception:
            m = re.search(r"```json\s*(\{.*?\})\s*```", raw_A, flags=re.DOTALL|re.IGNORECASE)
            if m:
                try: parsed_A = json.loads(m.group(1))
                except Exception: parsed_A = {}
            if not parsed_A:
                parsed_A = _kv_pairs_from_text(raw_A) or {}
    _write_text(project_name, "debug_parsed_A.json", json.dumps(parsed_A, ensure_ascii=False, indent=2))

    # ---------- Pass B: tech/targets/booleans ----------
    selected_B: List[Dict[str, Any]] = []
    for f, kws in FIELD_KWS_B.items():
        selected_B.extend(_pick_pages_for(kws, k=1, prefer_domain=main_domain))
    seen = set(); selected_B = [p for p in selected_B if (p.get("url") or "") not in seen and not seen.add(p.get("url") or "")]
    max_pages_B = 3 if provider != "openai" else 2
    selected_B = selected_B[:max_pages_B]

    context_B = "\n\n".join(_snip(p) for p in selected_B) if selected_B else ""
    if not context_B:
        fallback = [p for p in pages if (p.get("text") or "").strip()][:2]
        context_B = "\n\n".join(_snip(p) for p in fallback)

    tech_headers = [h for h in headers if h in [
        "Endorses/Uses ZKP","Has Exportable Credentials","Credential and Key Storage",
        "Targets Holders","Targets Issuers","Targets Verifiers",
        "Tech Stack Descriptions"
    ]]

    user_payload_B = {
        "project": project_name,
        "headers": tech_headers,
        "instructions": (
            "Return ONE JSON object only. Keys must match headers exactly. "
            "Use only the provided context. If not present, set 'Failed to disclose'. "
            "For every non-empty field, add an _evidence item with {field,value,source_url,source_type,confidence}."
        ),
        "context": context_B,
    }
    messages_B = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user_payload_B, ensure_ascii=False)},
    ]
    print(f"[extract] {project_name}: calling LLM (Pass B)…")
    try:
        raw_B = _chat_json(messages_B, provider=os.getenv("LLM_PROVIDER", PROVIDER).lower().strip(),
                           model=os.getenv("LLM_MODEL", OLLAMA_MODEL if provider != "openai" else OPENAI_MODEL).strip())
    except Exception as e:
        raw_B = ""
        _write_text(project_name, "llm_error_B.txt", f"{type(e).__name__}: {e}")
    _write_text(project_name, "llm_raw_B.json", raw_B or "")

    parsed_B = {}
    if raw_B.strip():
        try:
            parsed_B = json.loads(raw_B)
        except Exception:
            m = re.search(r"```json\s*(\{.*?\})\s*```", raw_B, flags=re.DOTALL|re.IGNORECASE)
            if m:
                try: parsed_B = json.loads(m.group(1))
                except Exception: parsed_B = {}
            if not parsed_B:
                parsed_B = _kv_pairs_from_text(raw_B) or {}
    _write_text(project_name, "debug_parsed_B.json", json.dumps(parsed_B, ensure_ascii=False, indent=2))

    # ---------- merge, coerce, normalize ----------
    merged = {}
    if isinstance(parsed_A, dict): merged.update(parsed_A)
    if isinstance(parsed_B, dict):
        for k, v in parsed_B.items():
            if k not in merged or not (isinstance(merged[k], str) and merged[k].strip()):
                merged[k] = v

    # collect evidence
    ev = []
    if isinstance(parsed_A, dict) and isinstance(parsed_A.get("_evidence"), list):
        ev.extend(parsed_A["_evidence"])
    if isinstance(parsed_B, dict) and isinstance(parsed_B.get("_evidence"), list):
        ev.extend(parsed_B["_evidence"])
    merged["_evidence"] = ev

    data = _coerce_to_headers(merged, headers, project_name=project_name)
    _ensure_name_alias(data, merged)

    # map evidence → "* Source"/"Live Source …"
    for e in (ev or []):
        f = (e.get("field") or "").strip()
        src = (e.get("source_url") or "").strip()
        if f and src:
            _set_source_for_field(data, f, src)

    # rollup sources (if column exists)
    rollup_col = None
    for h in headers:
        if h.lower() in ("sources (all)", "sources", "all sources"):
            rollup_col = h; break
    if rollup_col:
        urls = []
        for e in (ev or []):
            u = (e.get("source_url") or "").strip()
            if u and u not in urls: urls.append(u)
        # add snippet urls too
        for p in (selected_A + selected_B):
            u = (p.get("url") or "").strip()
            if u and u not in urls: urls.append(u)
        if urls:
            data[rollup_col] = ", ".join(urls)

    # normalize
    if "Status" in data:
        data["Status"] = normalize_status(data.get("Status"))
    for k in [
        "Endorses/Uses ZKP","Has Exportable Credentials","Credential and Key Storage",
        "Targets Holders","Targets Issuers","Targets Verifiers"
    ]:
        if k in data:
            data[k] = normalize_fd(data.get(k))
    for k in ["Announcement","Launch","Project Announcement Date","Project Launch Date"]:
        if k in data:
            data[k] = normalize_year(data.get(k))

    # ensure all headers present
    for h in headers:
        if h not in data: data[h] = ""

    # safety net: if still empty, at least set Website/brief from first page
    non_meta = [k for k in data.keys() if k != "_evidence"]
    if not any(isinstance(data.get(k), str) and data.get(k).strip() for k in non_meta):
        if pages:
            first = pages[0]
            url = (first.get("url") or "").strip()
            text = (first.get("text") or "").strip()
            for h in headers:
                if h.lower() == "website" and url:
                    data[h] = url; break
            desc = (text[:240] + "…") if len(text) > 240 else text
            for h in headers:
                if h.lower() in ("description","project description","brief"):
                    data[h] = desc; break
            if url:
                data["_evidence"] = data.get("_evidence", [])
                data["_evidence"].append({
                    "field": "Website","value": url,
                    "source_url": url, "source_type": "webpage","confidence": "medium"
                })

    filled = sum(1 for k, v in data.items() if k != "_evidence" and isinstance(v, str) and v.strip())
    print(f"[extract] {project_name}: fields filled={filled}")
    return data
