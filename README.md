# 🧠 Web of Trust POC  
### AI-Assisted Data Collection using Local Llama 3.1 (Ollama) or OpenAI (switchable at runtime)

This POC reads `data/input.xlsx`, searches the web, scrapes public pages, asks an LLM to extract structured fields, and writes `data/output.xlsx` with **AI_Data**, **Comparison**, and **Review** sheets.

## ⚡ Quick Start (with FULL PATH commands)

> Replace `<ABS_PATH>` with your actual project path. Example:  
> `/Users/you/Documents/workspace-py/wot-poc`

```bash
# 1) Go to your project
cd <ABS_PATH>/wot-poc

# 2) Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3) Install dependencies
/Users/<you>/Documents/workspace-py/wot-poc/.venv/bin/pip install -r requirements.txt

# 4) Configure .env
# Open .env and set:
#   LLM_PROVIDER=ollama  (for local model)  OR  LLM_PROVIDER=openai (for cloud)
#   OPENAI_API_KEY=sk-... (only if using OpenAI)

# 5A) Run with local Llama (Ollama)
# (macOS) Install/start Ollama once: brew install ollama && brew services start ollama
ollama pull llama3.1
python src/main.py --targets all --provider ollama --model llama3.1
ollama --model llama3.1

# 5B) Run with OpenAI (cloud)
export OPENAI_API_KEY=sk-...  # or set in .env
python src/main.py --targets all --provider openai --model gpt-4o-mini
openai --model gpt-4o-mini --temperature 0 --max-output-tokens 1200

# 6) Open results
open wot-poc/data/output.xlsx   # Windows: start data\output.xlsx
```

### Targeting specific projects (full paths)
```bash
python src/main.py --targets "cheqd,Diwala" --provider openai --model gpt-4o-mini

python src/main.py --targets all --provider ollama --model llama3.1
```

## 🔧 `.env` (create in project root)

```dotenv
USE_MANUAL_ONLY=false
MAX_URLS_PER_PROJECT=6

SEARCH_REGION=us-en
SEARCH_SAFESEARCH=moderate
SEARCH_BACKOFF_SECONDS=2
SEARCH_UA=Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36

SCRAPE_CONNECT_TIMEOUT=10
SCRAPE_READ_TIMEOUT=25
SCRAPE_MAX_RETRIES=3
SCRAPE_BACKOFF=0.5
SCRAPE_MAX_BYTES=2000000
SCRAPE_SLEEP_BETWEEN=0.4
SCRAPE_UA=Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36

# LLM provider (CLI flags override these)
LLM_PROVIDER=ollama                 # openai | ollama
OPENAI_API_KEY=                     # put your key here if using OpenAI
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0
OPENAI_MAX_OUTPUT_TOKENS=1200

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

**Where to put the OpenAI key?**  
Put `OPENAI_API_KEY=sk-...` in `.env` (recommended). You may also `export OPENAI_API_KEY=sk-...` in your terminal.

---

## 🧩 What you get

- **AI_Data** — fresh AI-extracted fields for every Product Name in `input.xlsx`
- **Comparison** — side-by-side Client vs AI with ✅/❌
- **Review** — values, source URLs, and validation notes

---

## 🧰 Troubleshooting

- **0 URLs found in search** → raise `SEARCH_BACKOFF_SECONDS` to 3–4; try a different network/VPN.
- **Scraper shows 0 chars** → pages are PDFs/JS-heavy; scraper logs `content-type` and char counts.
- **Provider switch** → use CLI overrides: `--provider openai|ollama`, `--model <name>`.

---

## 🗂 Folder Structure

```
wot-poc/
  ├─ data/
  │   ├─ input.xlsx
  │   ├─ output.xlsx
  │   └─ cache/<Project>/
  │      ├─ urls.json
  │      ├─ texts/*.json
  │      └─ record_debug.json
  ├─ src/
  │   ├─ main.py
  │   ├─ searcher.py
  │   ├─ scraper.py
  │   ├─ extractor.py
  │   ├─ schema.py
  │   ├─ validator.py
  │   ├─ export_excel.py
  │   └─ review_report.py
  ├─ .env
  ├─ requirements.txt
  └─ README.md
```