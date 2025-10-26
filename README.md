# 🧠 Web of Trust POC  
### AI-Assisted Data Collection using Local Llama 3.1 (via Ollama)

This Proof of Concept automatically collects and structures data for **Digital Identity (SSI/DI)** projects in the same Excel format used by the client.  
It reads the existing `input.xlsx`, identifies rows with missing fields, scrapes public information, asks a **local AI model (Llama 3.1)** to fill the gaps, and writes results to `output.xlsx`.

## 🚀 1  Prerequisites — Install Once

### A  Install Python 3.12 or newer
| Platform | Command / Download |
|-----------|--------------------|
| **macOS** | `brew install python`  or  [python.org/downloads](https://www.python.org/downloads/) |
| **Windows** | [Download Installer](https://www.python.org/downloads/windows/) → check ✅ *Add Python to PATH* |
| **Linux** | `sudo apt install python3 python3-venv python3-pip` |

Verify installation:
```bash
python3 --version
```

### B  Install Git (optional)
If you don’t have it installed:

| Platform | Command / Link |
|-----------|----------------|
| **macOS** | `brew install git` |
| **Windows** | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **Linux** | `sudo apt install git` |

### C  Install Ollama + Llama 3.1 (local model)
Ollama lets you run large language models locally — **no API key or internet dependency**.

#### macOS
```bash
brew install ollama
brew services start ollama        # run as background service
ollama pull llama3.1              # downloads ~4 GB model
```

#### Windows
1. Download & install → [ollama.com/download](https://ollama.com/download)  
2. Start Ollama from the Start Menu (it runs as a service)  
3. In PowerShell:
```powershell
ollama pull llama3.1
```

#### Linux
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1
```

Verify Ollama:
```bash
ollama run llama3.1 "Hello"
```
If it replies, you’re ready.

## 🗂  2  Project Setup

### A  Get the project folder
Download or clone this folder (for example to `~/wot-poc`).

```
wot-poc/
  data/
    input.xlsx        # client-provided workbook
  src/                # Python scripts
  README.md
  requirements.txt
  .env
```

### B  Create and activate a Python environment
```bash
cd ~/wot-poc
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## ⚙️  3  Configure the environment

Your `.env` file (included) should look like:
```
# ====== CORE PATHS / MODES ======
USE_MANUAL_ONLY=false             # we want real web search for the POC
MAX_URLS_PER_PROJECT=6

# ====== SEARCHER (HTML DDG/Bing fallback) ======
SEARCH_REGION=us-en               # for logs only
SEARCH_SAFESEARCH=moderate
SEARCH_BACKOFF_SECONDS=2          # increase to 3-4 if you see throttling
SEARCH_UA=Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36

# ====== SCRAPER ======
SCRAPE_CONNECT_TIMEOUT=10
SCRAPE_READ_TIMEOUT=25
SCRAPE_MAX_RETRIES=3
SCRAPE_BACKOFF=0.5
SCRAPE_MAX_BYTES=2000000
SCRAPE_SLEEP_BETWEEN=0.4
SCRAPE_UA=Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36

# ====== LLM PROVIDER SWITCH (CLI can override these) ======
LLM_PROVIDER=ollama               # openai | ollama
# -- OpenAI (used when LLM_PROVIDER=openai) --
OPENAI_API_KEY=                   # set this if you use OpenAI
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0
OPENAI_MAX_OUTPUT_TOKENS=1200

# -- Ollama (used when LLM_PROVIDER=ollama) --
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

> **Tip:**  
> To let the script find URLs automatically, set  
> `USE_MANUAL_ONLY=false`.

## 🧩  4  Running the POC

### Step 1  Start Ollama
```bash
ollama serve
```
*(or `brew services start ollama` on macOS)*

### Step 2  Activate the virtual environment
```bash
cd ~/wot-poc
source .venv/bin/activate
```

### Step 3  Ensure `data/input.xlsx` is the client file  
It should contain four filled sample rows and several blank rows for projects to be auto-filled.

### Step 4  Run the POC
```bash
Local (Ollama)
ollama pull llama3.1
python src/main.py --targets all --provider ollama --model llama3.1 # -- for all the Project ID's from Input.xlsx --
or
python src/main.py --targets "<NAME>" --provider ollama --model llama3.1 # -- for sepecfic Project ID's from Input.xlsx --

OpenAI:
export OPENAI_API_KEY=sk-...
python src/main.py --targets all --provider openai --model gpt-4o-mini # -- for all the Project ID's from Input.xlsx --
or
python src/main.py --targets "<NAME>" --provider openai --model gpt-4o-mini # -- for sepecfic Project ID's from Input.xlsx --
```

What happens:
1. The script reads `input.xlsx` and detects rows that have names but mostly empty fields.  
2. For each project name, it scrapes or loads URLs (or reads `manual_urls.txt` if provided).  
3. It asks Llama 3.1 to extract structured data.  
4. It merges results into `data/output.xlsx`, keeping existing data intact.  
5. It adds a **Review** sheet listing evidence URLs and validation notes.

## 🌐  5  (Recommended) Add Manual URLs
You can guide the model by supplying 2-4 official sources per project:

```bash
mkdir -p data/cache/Your_Project_Name
nano data/cache/Your_Project_Name/manual_urls.txt
```

Each line = one URL (e.g., official site, press release, GitHub repo).

When `USE_MANUAL_ONLY=true`, the script uses only these URLs — no online search.

## 📄  6  Outputs

| File | Description |
|------|--------------|
| **data/output.xlsx** | Final results merged into the original structure |
| **Review sheet** | Field values, source URLs, and validation issues |
| **data/cache/<Project>/record_debug.json** | Full AI JSON response |
| **data/cache/<Project>/texts/*.json** | Scraped text from each page |

## 🧰  7  Troubleshooting

| Issue | Solution |
|-------|-----------|
| `ModuleNotFoundError` | Activate the venv → `pip install -r requirements.txt` |
| `could not connect to localhost:11434` | Start Ollama: `ollama serve` |
| First run very slow | Normal — model loads into memory (1-2 min) |
| Output blank | Add manual URLs or set `USE_MANUAL_ONLY=true` |
| Wrong Python path (macOS/Xcode) | Use `python3` explicitly (from Homebrew) |

## 🧾  8  Example Run Log

```
$ ollama serve
$ source .venv/bin/activate
$ python src/main.py --targets auto

[auto] detected targets: ['Mina', 'eIDAS Bridge', 'Verida', 'ID2020']
Processing Mina
Processing eIDAS Bridge
Processing Verida
Processing ID2020
Done → /Users/<user>/wot-poc/data/output.xlsx
```

Open `data/output.xlsx` → check both the **main sheet** and **Review** sheet.

## 💡  9  Tips for Better Results
- Always include a few good manual URLs for each blank project.  
- Increase `MAX_URLS_PER_PROJECT` to 6–8 for richer context.  
- Once stable, raise snippet length in `extractor.py` for deeper extractions.  
- For debugging, open `data/cache/<Project>/record_debug.json`.  

## 🧩  10  Folder Structure

```
wot-poc/
  ├─ data/
  │   ├─ input.xlsx
  │   ├─ output.xlsx
  │   └─ cache/<Project>/
  │       ├─ manual_urls.txt
  │       ├─ urls.json
  │       ├─ texts/*.json
  │       └─ record_debug.json
  ├─ src/
  │   ├─ main.py
  │   ├─ extractor.py
  │   ├─ scraper.py
  │   ├─ searcher.py
  │   ├─ schema.py
  │   ├─ validator.py
  │   ├─ export_excel.py
  │   └─ review_report.py
  ├─ .env
  ├─ requirements.txt
  └─ README.md
```

## ✅  11  Summary

After following this README, **anyone** can:
1. Install Python and Ollama.  
2. Pull and run the Llama 3.1 model locally.  
3. Run `python src/main.py --targets auto`.  
4. Open `data/output.xlsx` to review AI-filled fields and evidence.

No API keys, no cloud usage — everything runs **locally** and reproducibly.