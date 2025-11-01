# src/app/workers/llm_client.py
from __future__ import annotations

import os
import json
import time
from typing import Dict, List, Any, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.utils.logger import get_logger

log = get_logger("llm_client")

# ---------------------------
# Config
# ---------------------------

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

# Soft character budget for the whole request payload (messages)
# We keep this simple/robust vs. depending on tokenizer availability.
MAX_REQUEST_CHARS = int(os.getenv("LLM_MAX_REQUEST_CHARS", "12000"))
DEFAULT_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "512"))

# ---------------------------
# Helpers
# ---------------------------

def _coerce_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Validate + normalize messages into [{'role': 'user'|'system'|'assistant', 'content': '...'}].
    """
    out: List[Dict[str, str]] = []
    for i, m in enumerate(messages or []):
        if not isinstance(m, dict):
            log.warning("Skipping non-dict message at idx=%s", i)
            continue
        role = (m.get("role") or "").strip()
        content = m.get("content")
        if role not in {"system", "user", "assistant"}:
            log.warning("Skipping message idx=%s with invalid role=%r", i, role)
            continue
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        out.append({"role": role, "content": content})
    if not out:
        raise ValueError("No valid chat messages provided.")
    return out


def _clip_messages_by_chars(messages: List[Dict[str, str]], max_chars: int) -> List[Dict[str, str]]:
    """
    Cheap safety guard: ensure total char budget stays under max_chars.
    Keeps earlier messages; truncates the last one if needed.
    """
    total = 0
    clipped: List[Dict[str, str]] = []
    for m in messages:
        c = m["content"]
        need = len(c)
        if total + need <= max_chars:
            clipped.append(m)
            total += need
        else:
            # truncate last content to remaining budget (if any)
            remain = max_chars - total
            if remain > 16:  # only keep if there's some meaningful room
                clipped.append({"role": m["role"], "content": c[:remain]})
            break
    return clipped


def _is_transient_error(exc: Exception) -> bool:
    """
    Decide whether an error is worth retrying.
    - HTTP 429 (rate limit), 5xx, timeouts, connection errors → retry
    - HTTP 400 (bad request) → usually not retryable unless from Ollama flaky reads
    """
    # requests exceptions
    if isinstance(exc, requests.Timeout):
        return True
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.HTTPError):
        try:
            status = exc.response.status_code  # type: ignore[union-attr]
        except Exception:
            status = None
        if status in {408, 409, 429, 500, 502, 503, 504}:
            return True
        # Sometimes Ollama returns 400 when the server is still warming up; one retry can help.
        if status == 400 and "ollama" in str(exc).lower():
            return True
        return False
    # OpenAI httpx transport sometimes bubbles as RequestError/Timeout
    # (we rely on the HTTPError / Timeout cases above via requests for Ollama;
    # for OpenAI we'll catch and wrap as RuntimeError below)
    return False


def _select_model(provider: str, model: Optional[str]) -> str:
    if provider == "openai":
        return (model or DEFAULT_OPENAI_MODEL).strip()
    # default to Ollama otherwise
    return (model or DEFAULT_OLLAMA_MODEL).strip()


# ---------------------------
# OpenAI client
# ---------------------------

def _openai_chat(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
    top_p: Optional[float] = None,
    timeout: int = 60,
) -> str:
    """
    Call OpenAI Chat Completions (openai>=1.x).
    """
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("OpenAI SDK not installed. `pip install openai`") from e

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key).with_options(timeout=timeout)

    # API expects a list of dicts
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if top_p is not None:
        payload["top_p"] = top_p

    log.debug("OpenAI request: model=%s, msgs=%d, max_tokens=%s", model, len(messages), max_tokens)

    try:
        resp = client.chat.completions.create(**payload)  # type: ignore[arg-type]
    except Exception as e:
        # Let caller decide (tenacity will retry on wrapped errors)
        raise RuntimeError(f"OpenAI request failed: {e}") from e

    try:
        content = (resp.choices[0].message.content or "").strip()  # type: ignore[attr-defined]
    except Exception as e:
        raise RuntimeError(f"Malformed OpenAI response: {e}") from e

    return content


# ---------------------------
# Ollama client
# ---------------------------

def _ollama_chat(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
    top_p: Optional[float] = None,
    timeout: int = 60,
) -> str:
    """
    Call Ollama /api/chat. Works for llama, gemma, etc (as long as the model is pulled in Ollama).
    """
    url = f"{OLLAMA_HOST}/api/chat"
    options: Dict[str, Any] = {}
    if max_tokens is not None:
        # Ollama uses num_predict (# of tokens to generate)
        options["num_predict"] = int(max_tokens)
    if temperature is not None:
        options["temperature"] = float(temperature)
    if top_p is not None:
        options["top_p"] = float(top_p)

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": options or None,
    }

    log.debug("Ollama request: host=%s model=%s msgs=%d", OLLAMA_HOST, model, len(messages))

    try:
        r = requests.post(url, json=payload, timeout=timeout)
        if r.status_code != 200:
            # Raise for retry logic to kick in if transient
            try:
                r.raise_for_status()
            except Exception as e:
                raise requests.HTTPError(f"Ollama HTTP {r.status_code}: {r.text}", response=r) from e
        data = r.json()
    except Exception as e:
        # bubble up for retry
        raise

    # Expected schema: {"message": {"role":"assistant","content":"..."}}
    try:
        msg = data.get("message") or {}
        content = (msg.get("content") or "").strip()
        # Some versions return `content` at top-level as well
        if not content and "content" in data:
            content = str(data["content"]).strip()
        return content
    except Exception as e:
        raise RuntimeError(f"Malformed Ollama response: {e}") from e


# ---------------------------
# Retry-enabled front door
# ---------------------------

def _call_with_provider(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
    top_p: Optional[float] = None,
    timeout: int = 60,
) -> str:
    if provider == "openai":
        return _openai_chat(messages, model, max_tokens=max_tokens, temperature=temperature, top_p=top_p, timeout=timeout)
    # default: ollama
    return _ollama_chat(messages, model, max_tokens=max_tokens, temperature=temperature, top_p=top_p, timeout=timeout)


@retry(
    reraise=True,
    retry=retry_if_exception(_is_transient_error),
    stop=stop_after_attempt(int(os.getenv("LLM_RETRY_ATTEMPTS", "3"))),
    wait=wait_exponential(multiplier=1, min=1, max=12),
)
def _chat_json_once(
    messages: List[Dict[str, Any]],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
    top_p: Optional[float] = None,
    timeout: int = 60,
) -> str:
    """
    One attempt (tenacity wraps this). Returns the assistant content string.
    Raises on hard errors; transient ones are retried by tenacity.
    """
    prov = (provider or DEFAULT_PROVIDER).strip().lower()
    mdl = _select_model(prov, model)

    msgs = _coerce_messages(messages)
    msgs = _clip_messages_by_chars(msgs, MAX_REQUEST_CHARS)

    # OpenAI 400s often come from exceeding context or bad param combos —
    # the simple clip above avoids most issues. If it still happens, it will raise
    # and tenacity won't retry (400 is non-transient) — that’s fine & visible in logs.
    content = _call_with_provider(
        provider=prov,
        model=mdl,
        messages=msgs,
        max_tokens=(max_tokens if max_tokens is not None else DEFAULT_MAX_OUTPUT_TOKENS),
        temperature=temperature,
        top_p=top_p,
        timeout=timeout,
    )

    log.debug("LLM(%s/%s) content chars=%d", prov, mdl, len(content or ""))
    return content or ""


# ---------------------------
# Public API
# ---------------------------

def chat_json(
    messages: List[Dict[str, Any]],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
    top_p: Optional[float] = None,
    timeout: int = 60,
) -> str:
    """
    Unified chat call.
    - provider: "openai" | "ollama" (default from env LLM_PROVIDER)
    - model:    e.g. "gpt-4o-mini" | "llama3.1:8b" | "gemma2:9b-instruct" (via Ollama)
    - returns raw assistant string (not parsed)
    """
    t0 = time.time()
    prov = (provider or DEFAULT_PROVIDER).strip().lower()
    mdl = _select_model(prov, model)

    try:
        out = _chat_json_once(
            messages=messages,
            provider=prov,
            model=mdl,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout=timeout,
        )
        log.info("LLM OK (%s/%s) %.2fs chars=%d", prov, mdl, time.time() - t0, len(out or ""))
        return out
    except Exception as e:
        # We do NOT print; we log the error and re-raise for the caller to decide.
        log.error("LLM FAILED (%s/%s): %s", prov, mdl, e, exc_info=True)
        raise
