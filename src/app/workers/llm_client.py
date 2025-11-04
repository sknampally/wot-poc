"""
LLM (Large Language Model) client for OpenAI, Ollama, and Perplexity.

This module provides a unified interface for calling LLM APIs:
- OpenAI: Official OpenAI API (requires OPENAI_API_KEY)
- Ollama: Local LLM server (requires running ollama serve)
- Perplexity: Web-grounded AI with real-time search (requires PERPLEXITY_API_KEY)

The chat_json() function handles retries automatically for transient failures.

Usage:
    from app.workers.llm_client import chat_json
    
    response = chat_json(
        system="You are a helpful assistant",
        user="Extract data from this text: ...",
        provider="openai",
        model="gpt-4o-mini",
        max_tokens=2000
    )
"""
from __future__ import annotations
import os
import logging
from typing import List, Dict, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# OpenAI client (official 1.x SDK)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

# Perplexity: Uses OpenAI-compatible API (no separate SDK needed)

# Optional: minimal Ollama fallback
import json
import requests


def _openai_chat(messages: List[Dict[str, str]], model: str, max_tokens: int, temperature: float = 0) -> str:
    """
    Call OpenAI Chat Completions API.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        model: Model name (e.g., 'gpt-4o-mini', 'gpt-4')
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature (0 = deterministic, higher = more creative)
    
    Returns:
        str: The generated text response
    
    Raises:
        RuntimeError: If OpenAI package not installed or API key missing
    """
    if OpenAI is None:
        raise RuntimeError("openai package not available. Install 'openai>=1.12.0'.")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)
    
    # Make API call
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "text"},  # Get plain text, not JSON
    )
    
    # Extract response text
    txt = (resp.choices[0].message.content or "").strip()
    return txt


def _ollama_chat(messages: List[Dict[str, str]], model: str, max_tokens: int, temperature: float = 0) -> str:
    """
    Call Ollama local LLM API.
    
    Requires a running Ollama server (`ollama serve`).
    Reads OLLAMA_HOST from environment (default: http://localhost:11434/api/chat).
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        model: Model name (e.g., 'llama3.1', 'mistral')
        max_tokens: Maximum tokens in response (num_predict in Ollama)
        temperature: Sampling temperature
    
    Returns:
        str: The generated text response
    
    Raises:
        requests.RequestException: If Ollama server not reachable
    """
    # Get Ollama server URL from environment
    url = os.getenv("OLLAMA_HOST", "http://localhost:11434/api/chat")
    
    # Build request payload
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,  # Get complete response, not streamed
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,  # Ollama uses 'num_predict' instead of 'max_tokens'
        },
    }
    
    # Make POST request to Ollama API
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    
    # Parse response (Ollama API format)
    # New format: {"message":{"content":...}}
    if isinstance(data, dict):
        msg = data.get("message") or {}
        content = msg.get("content") or ""
        if content:
            return content
        
        # Legacy format fallback (unlikely but handle it)
        if "choices" in data:
            try:
                return data["choices"][0]["message"]["content"]
            except Exception:
                pass
    
    # Last resort: return JSON string representation
    return json.dumps(data)[:8000]


def _perplexity_chat(messages: List[Dict[str, str]], model: str, max_tokens: int, temperature: float = 0) -> tuple[str, List[str]]:
    """
    Call Perplexity Chat Completions API.
    
    Uses OpenAI-compatible API (base_url = https://api.perplexity.ai).
    Requires PERPLEXITY_API_KEY in environment.
    Provides web-grounded responses with real-time search capabilities.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        model: Model name (e.g., 'sonar', 'sonar-pro', 'sonar-reasoning')
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature
    
    Returns:
        tuple[str, List[str]]: (response_text, list_of_citation_urls)
    
    Raises:
        RuntimeError: If OpenAI package not installed or API key missing
    """
    if OpenAI is None:
        raise RuntimeError("openai package not available. Install 'openai>=1.12.0'.")
    
    api_key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("PERPLEXITY_API_KEY is not set")
    
    # Initialize OpenAI client with Perplexity's base URL
    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
    
    # Make API call
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    
    # Extract response text
    txt = (resp.choices[0].message.content or "").strip()
    
    # Extract citations from Perplexity response
    # Perplexity typically includes citations in the text as:
    # - [1] https://example.com
    # - Or at the end: Sources: [1] https://example.com [2] https://other.com
    # - Or inline: ...according to [1](https://example.com)...
    citations: List[str] = []
    
    # Try to get citations from response object (if available)
    if hasattr(resp, 'citations') and resp.citations:
        citations = list(resp.citations) if isinstance(resp.citations, (list, tuple)) else [str(resp.citations)]
    else:
        # Extract citations from response text
        import re
        # Pattern 1: [1] https://example.com or [1](https://example.com)
        citation_pattern = r'\[\d+\]\(?(https?://[^\s\)\]\n]+)\)?'
        found_urls = re.findall(citation_pattern, txt)
        if found_urls:
            citations.extend(found_urls)
        
        # Pattern 2: Look for URLs after "Sources:" or "References:" markers
        sources_section = re.search(r'(?:Sources?|References?):\s*(.*)', txt, re.IGNORECASE | re.DOTALL)
        if sources_section:
            sources_text = sources_section.group(1)
            urls_in_section = re.findall(r'https?://[^\s\]\n]+', sources_text)
            citations.extend(urls_in_section)
        
        # Pattern 3: Extract all URLs from the response (fallback)
        # This is less precise but catches any URLs mentioned
        if not citations:
            all_urls = re.findall(r'https?://[^\s\)\]\n,;]+', txt)
            # Deduplicate and filter out common false positives
            seen = set()
            for url in all_urls:
                url_clean = url.rstrip('.,;!?)')
                # Filter out image URLs, data URIs, and other non-document URLs
                if url_clean not in seen and not any(skip in url_clean.lower() for skip in ['.png', '.jpg', '.jpeg', '.svg', '.gif', 'data:']):
                    citations.append(url_clean)
                    seen.add(url_clean)
    
    # Remove duplicates while preserving order
    citations = list(dict.fromkeys(citations))
    
    return txt, citations


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def chat_json(
    *,
    messages: Optional[List[Dict[str, str]]] = None,
    system: Optional[str] = None,
    user: Optional[str] = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    max_tokens: int = 800,
    temperature: float = 0.0,
    return_citations: bool = False,
) -> str | tuple[str, List[str]]:
    """
    Unified chat wrapper for LLM APIs.
    
    Automatically retries on transient failures (up to 3 attempts with exponential backoff).
    
    You can pass messages in two ways:
    1. Full messages list: messages=[{"role":"system","content":"..."}, ...]
    2. Convenience params: system="...", user="..." (builds messages automatically)
    
    Args:
        messages: Optional full messages list (if provided, system/user ignored)
        system: Optional system message (role: "system")
        user: Optional user message (role: "user")
        provider: LLM provider - 'openai', 'ollama', or 'perplexity'
        model: Model name (e.g., 'gpt-4o-mini', 'llama3.1', 'sonar')
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature (0 = deterministic)
        return_citations: If True and provider is 'perplexity', returns tuple (text, citations). Default False.
    
    Returns:
        str: Raw text response from LLM (default)
        tuple[str, List[str]]: (text, citations) if return_citations=True and provider='perplexity'
    
    Raises:
        ValueError: If no messages provided or unsupported provider
        RuntimeError: If API key missing (OpenAI) or server unreachable (Ollama)
    
    Example:
        # Using convenience params
        response = chat_json(
            system="You are a data extraction expert",
            user="Extract the mission statement from: ...",
            provider="openai",
            model="gpt-4o-mini"
        )
        
        # Using full messages
        response = chat_json(
            messages=[
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"}
            ]
        )
    """
    # Build messages list
    msgs: List[Dict[str, str]] = []
    if messages:
        # Use provided messages as-is
        msgs = messages
    else:
        # Build messages from system/user convenience params
        if system:
            msgs.append({"role": "system", "content": system})
        if user:
            msgs.append({"role": "user", "content": user})
        if not msgs:
            raise ValueError("chat_json requires either messages or (system/user) content")

    # Call appropriate provider
    prov = (provider or "openai").strip().lower()
    if prov == "openai":
        return _openai_chat(msgs, model=model, max_tokens=max_tokens, temperature=temperature)
    elif prov == "ollama":
        return _ollama_chat(msgs, model=model, max_tokens=max_tokens, temperature=temperature)
    elif prov == "perplexity":
        text, citations = _perplexity_chat(msgs, model=model, max_tokens=max_tokens, temperature=temperature)
        # Return tuple if citations requested, otherwise just text (for backward compatibility)
        if return_citations:
            return text, citations
        else:
            return text
    else:
        raise ValueError(f"Unsupported provider: {provider}")
