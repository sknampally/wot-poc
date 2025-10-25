import os, json, requests
from typing import Dict,Any,List
from dotenv import load_dotenv
from tenacity import retry,stop_after_attempt,wait_exponential
from schema import normalize_status,normalize_fd,normalize_year, _name_header

load_dotenv()
OLLAMA_HOST=os.getenv("OLLAMA_HOST","http://localhost:11434").rstrip("/")
OLLAMA_MODEL=os.getenv("OLLAMA_MODEL","llama3.1")

SYSTEM=("You are a meticulous research assistant for digital identity projects (SSI/DI). "
"Use ONLY provided context. If unknown, use 'Failed to disclose'. Return STRICT JSON keyed by headers. "
"Include `_evidence` list with field,value,source_url,source_type,confidence.")

def _repair_json(s):
    try: json.loads(s); return s
    except: pass
    start,end=s.find("{"),s.rfind("}")
    if start!=-1 and end>start:
        try: json.loads(s[start:end+1]); return s[start:end+1]
        except: pass
    return "{}"

def _ollama_chat_json(messages):
    url=f"{OLLAMA_HOST}/api/chat"
    payload={
        "model":OLLAMA_MODEL,
        "messages":messages,
        "stream":False,
        "format":"json",
        "options":{"temperature":0.0,"num_ctx":4096,"num_predict":512}
    }
    try:
        r=requests.post(url,json=payload,timeout=600); r.raise_for_status()
        j=r.json()
        return j.get("message",{}).get("content","") or j.get("response","")
    except requests.exceptions.ReadTimeout:
        import time; time.sleep(5)
        r=requests.post(url,json=payload,timeout=600); r.raise_for_status()
        j=r.json()
        return j.get("message",{}).get("content","") or j.get("response","")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def extract_record(project_name: str, headers: List[str], pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Build compact context (first 2 pages, short snippets) for reliability
    snippets=[]
    for p in pages:
        t=p.get("text",""); u=p.get("url","")
        if t: snippets.append(f"[URL]{u}\n{t[:900]}")
        if len(snippets)>=2: break

    # If we have no context at all, return a skeleton so the row writes with the name column set
    name_h = _name_header(headers)
    if not snippets:
        data={h:"" for h in headers}
        data[name_h]=project_name
        data["_evidence"]=[]
        return data

    context="\n\n".join(snippets)
    user_payload={
        "project": project_name,
        "headers": headers,
        "instructions": (
            "Use allowed enums when obvious: "
            "Status=[Announced,Pilot,Launched,Discontinued]. "
            "Ternary fields use [True,False,Failed to disclose]. "
            "Year fields should be a 4-digit year when discoverable. "
            "Every non-empty value must include an evidence entry in _evidence with a source_url from the context."
        ),
        "context": context
    }
    messages=[{"role":"system","content":SYSTEM},{"role":"user","content":json.dumps(user_payload)}]

    raw=_ollama_chat_json(messages); raw=_repair_json(raw)
    data=json.loads(raw or "{}")

    # normalizations
    if "Status" in data: data["Status"]=normalize_status(data.get("Status"))
    for k in["Endorses/Uses ZKP","Has Exportable Credentials","Credential and Key Storage",
             "Targets Holders","Targets Issuers","Targets Verifiers"]:
        if k in data: data[k]=normalize_fd(data.get(k))
    for k in["Announcement","Launch"]:
        if k in data: data[k]=normalize_year(data.get(k))

    # evidence safe-guard
    ev=data.get("_evidence",[])
    if not isinstance(ev,list): ev=[]
    data["_evidence"]=ev

    # ensure all headers present
    for h in headers:
        if h not in data: data[h]=""

    # ensure the correct name column is set
    if not data.get(name_h): data[name_h]=project_name

    # optional: place first evidence URLs into "<Field> Source" columns when they exist
    if ev:
        headers_lower = {h.lower(): h for h in headers}
        for e in ev:
            f = (e.get("field") or "").strip()
            src = (e.get("source_url") or "").strip()
            if not f or not src: continue
            src_col = f"{f} Source"
            if src_col.lower() in headers_lower:
                data[headers_lower[src_col.lower()]] = src

    return data
