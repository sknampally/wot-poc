# src/app/workers/llm_client.py
from __future__ import annotations

import os
from typing import List, Dict, Any, Optional

from app.utils.logger import get_logger

log = get_logger("llm_client")

def _validate_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Coerce each message to {'role': str, 'content': str}.
    Any non-string content becomes JSON string.
    """
    import json
    out: List[Dict[str, str]] = []
    for i, m in enumerate(messages):
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        out.append({"role": role, "content": content})
    return out

def _clip_content_tokensish(text: str, max_chars: int = 10_000) -> str:
    """
    Cheap guard so we don't blow up request size (a common source of 400s).
    """
    if not text:
        return text
    return text if len(text) <= max_chars else (text[:max_chars] + "…[truncated]")

def chat_json(messages: List[Dict[str, Any]], provider: str, model: str, max_tokens: int = 1200) -> Optional[str]:
    """
    Return assistant content (string) or None.
    Raises on network/HTTP errors so callers can log .exception() with stack.
    """
    provider = (provider or "openai").lower().strip()
    if provider != "openai":
        raise NotImplementedError(f"Provider '{provider}' not implemented in llm_client")

    # --- OpenAI SDK v1.x
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    # Optional: allow custom endpoint (rarely needed unless Azure/Proxy)
    base_url = os.getenv("OPENAI_API_BASE", None)
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

    msgs = _validate_messages(messages)

    # Defensive clipping of the largest message (usually the user context)
    if msgs:
        biggest = max(range(len(msgs)), key=lambda i: len(msgs[i]["content"] or ""))
        msgs[biggest]["content"] = _clip_content_tokensish(msgs[biggest]["content"], max_chars=18000)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=0.0,
            max_tokens=max_tokens,      # chat.completions uses 'max_tokens'
            n=1,
        )
        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        return content
    except Exception as e:
        # When OpenAI returns 400, SDK wraps it in an exception; log the body if present.
        # The extractor will call log.exception(), so just re-raise.
        # We still include a breadcrumb here.
        log.error("OpenAI chat.completions error (model=%s): %s", model, e)
        raise
