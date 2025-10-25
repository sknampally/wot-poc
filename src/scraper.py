import time,json,requests
from pathlib import Path
from bs4 import BeautifulSoup
import trafilatura
from tenacity import retry,stop_after_attempt,wait_fixed
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; wot-poc/1.0)"}

@retry(stop=stop_after_attempt(3),wait=wait_fixed(1))
def fetch(u,timeout=20):
    r=requests.get(u,headers=HEADERS,timeout=timeout)
    r.raise_for_status();return r.text

def clean_html(html):
    txt=trafilatura.extract(html,include_comments=False,no_fallback=True)
    if txt: return txt
    soup=BeautifulSoup(html,"lxml")
    [t.decompose() for t in soup(["script","style","noscript"])]
    return soup.get_text(" ",strip=True)

def scrape_urls(urls,out_dir:Path):
    out=[];tdir=out_dir/"texts";tdir.mkdir(parents=True,exist_ok=True)
    for i,item in enumerate(urls,1):
        url=item["url"];f=tdir/f"{i:02d}.json"
        if f.exists():out.append(json.loads(f.read_text()));continue
        try:
            html=fetch(url);text=clean_html(html)
            meta={"url":url,"title":item.get("title",""),"text":text[:100000]}
            f.write_text(json.dumps(meta,ensure_ascii=False,indent=2))
            out.append(meta);time.sleep(0.3)
        except Exception as e:
            out.append({"url":url,"error":str(e),"text":""})
    return out
