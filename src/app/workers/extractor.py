from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_fixed

from app.config.codebook import load_codebook
from app.core.schema import load_headers
from app.workers.llm_client import chat_json  # your unified client

log = logging.getLogger(__name__)

FAILED = "Failed to disclose"


def _shorten(text: str, max_chars: int = 3000) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return head + "\n...\n" + tail


def _compose_prompt(project: str, headers: List[str], pages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    # Gather a few page snippets
    snippets: List[str] = []
    for p in pages:
        t = (p.get("text") or "").strip()
        if not t:
            continue
        u = p.get("url", "")
        snippets.append(f"[URL]{u}\n{_shorten(t, 3000)}")
        if len(snippets) >= 3:
            break

    if not snippets:
        return [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "project": project,
                        "headers": headers,
                        "instructions": (
                            "No webpage context was available. "
                            "Return exactly one JSON object enclosed in <JSON>...</JSON> "
                            "with the given headers. Set everything to 'Failed to disclose'."
                        ),
                        "context": "",
                    }
                ),
            }
        ]

    instructions = (
        "Return ONLY one JSON object enclosed in <JSON>...</JSON>.\n"
        "Set 'Product Name' exactly to the project value.\n"
        "Allowed Status: [Announced,Pilot,Launched,Discontinued].\n"
        "Ternary fields: [True,False,Failed to disclose]. Year fields are 4 digits.\n"
        "Every non-empty field MUST include evidence URL present in the provided context in a list `_evidence` "
        "with objects: {field, value, source_url, source_type='webpage', confidence}."
    )

    return [
        {
            "role": "system",
            "content": (
                "You are a careful data extraction agent for a Digital Identity codebook. "
                "You MUST return EXACTLY one JSON object enclosed in <JSON>...</JSON>. "
                "Every non-empty field MUST cite an evidence URL present in the provided context."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "project": project,
                    "headers": headers,
                    "instructions": instructions,
                    "context": "\n\n".join(snippets),
                }
            ),
        },
    ]


def _empty_record(headers: List[str], project: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {h: FAILED for h in headers}
    # If these exist in headers, set friendly defaults
    if "Product Name" in out:
        out["Product Name"] = project
    if "Website" in out:
        out["Website"] = FAILED
    out["_evidence"] = []
    return out


@retry(stop=stop_after_attempt(2), wait=wait_fixed(1))
def extract_record(
    project: str,
    headers: List[str],
    pages: List[Dict[str, Any]],
    codebook: Optional[Dict[str, Any]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = 800,
    **_ignore,
) -> Dict[str, Any]:
    """
    Extract a single project's record.
    Accepts provider/model/max_output_tokens to match main.py call signature.
    """
    messages = _compose_prompt(project, headers, pages)

    try:
        raw = chat_json(
            messages=messages,
            provider=provider or "openai",
            model=model or "gpt-4o-mini",
            max_tokens=max_output_tokens or 800,
            temperature=0,
        )
        text = (raw or "").strip()
        if not text:
            log.info("[extract] %s: empty LLM response → using empty record", project)
            return _empty_record(headers, project)

        # salvage <JSON> ... </JSON>
        import re
        m = re.search(r"<JSON>\s*(\{.*\})\s*</JSON>", text, re.DOTALL)
        payload = m.group(1) if m else text

        data = json.loads(payload)
        # Map only known headers; put missing as 'Failed to disclose'
        out = _empty_record(headers, project)
        for k, v in (data or {}).items():
            if k in out and k != "_evidence":
                out[k] = v if (v not in (None, "", [])) else FAILED
        # Evidence passthrough
        if isinstance(data.get("_evidence"), list):
            out["_evidence"] = data["_evidence"]

        # Basic sanity: ensure Product Name
        if "Product Name" in out:
            out["Product Name"] = project

        filled = sum(1 for h in headers if out.get(h) not in (FAILED, None, ""))
        log.info("[extract] %s: fields filled=%d", project, filled)
        return out
    except Exception as e:
        log.exception("LLM mapping failed for %s: %s", project, e)
        return _empty_record(headers, project)
