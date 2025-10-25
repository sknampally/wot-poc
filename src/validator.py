import re,requests
from schema import STATUS_ENUM, FD, DLT_AVAIL_ENUM

def url_ok(u):
    try:
        if not u.startswith("http"):return False
        r=requests.head(u,timeout=10,allow_redirects=True)
        return r.status_code<400
    except:return False

def validate_record(rec):
    issues=[]
    if rec.get("Status") and rec["Status"] not in STATUS_ENUM:
        issues.append(f"Status bad:{rec['Status']}")
    for f in["Endorses/Uses ZKP","Has Exportable Credentials","Credential and Key Storage",
             "Targets Holders","Targets Issuers","Targets Verifiers"]:
        if rec.get(f) and rec[f] not in FD:
            issues.append(f"{f} bad:{rec[f]}")
    if rec.get("DLT Data Availability") and rec["DLT Data Availability"] not in DLT_AVAIL_ENUM:
        issues.append("DLT availability unexpected")
    for y in["Announcement","Launch"]:
        v=rec.get(y,"")
        if v and not re.match(r"^(19|20)\d{2}$",str(v)):issues.append(f"{y} not year:{v}")
    ws=rec.get("Website","")
    if ws and not url_ok(ws):issues.append("Website not reachable")
    return {"ok":not issues,"issues":issues}
