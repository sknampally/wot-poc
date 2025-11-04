# 🧠 Web of Trust POC  
### AI-Assisted Data Collection for Digital Identity Projects

This Proof of Concept automatically collects and structures data for **Digital Identity (SSI/DI)** projects using web search (SerpAPI), web scraping, and LLM-based extraction. It reads project names from `input.xlsx`, searches the internet for relevant sources, scrapes content, and uses an LLM to extract structured data into `output.xlsx` with source tracking and comparison against manual data.

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
If you don't have it installed:

| Platform | Command / Link |
|-----------|----------------|
| **macOS** | `brew install git` |
| **Windows** | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **Linux** | `sudo apt install git` |

### C  API Keys Required
This POC uses **SerpAPI** for Google search and **OpenAI** (or Ollama) for LLM extraction.

1. **SerpAPI**: Get free API key from [serpapi.com](https://serpapi.com) (or use Pro for better results)
2. **OpenAI**: Get API key from [platform.openai.com](https://platform.openai.com) (optional - can use Ollama locally)

## 🗂  2  Project Setup

### A  Get the project folder
```bash
git clone <repository-url>
cd wot-poc
```

### B  Create and activate a Python environment
```bash
cd wot-poc
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## ⚙️  3  Configure the environment

Create a `.env` file in the project root with:

```env
# ====== API Keys ======
SERPAPI_API_KEY=your_serpapi_key_here          # Required for web search
OPENAI_API_KEY=your_openai_key_here            # Required for LLM extraction

# ====== LLM Configuration ======
LLM_PROVIDER=openai                            # openai | ollama
LLM_MODEL=gpt-4o-mini                          # Model name
LLM_MAX_TOKENS=4000                            # Maximum output tokens

# ====== Search Configuration ======
MAX_URLS_PER_PROJECT=50                        # Maximum URLs to collect per project

# ====== Optional: Auto-Accuracy Check ======
AUTO_CHECK_ACCURACY=false                      # Set to 'true' to auto-run accuracy check after extraction (optional, backward compatibility)
                                                # Alternatively, use --with-accuracy-check flag when running extraction

# ====== Optional: Ollama (local LLM) ======
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

## 📋  4  Codebook / Data Definitions

The system uses a **codebook** (field definitions) from `data/wot_data_definations.xlsx` to guide extraction.

### Import Data Definitions
If stakeholders update the Excel file with data definitions, convert it to the codebook format:

```bash
# Method 1: Using main.py (recommended)
python src/main.py --import-codebook data/wot_data_definations.xlsx

# Method 2: Direct import utility
python -m app.utils.codebook_import data/wot_data_definations.xlsx
```

This creates `data/codebook.json` which the system automatically uses for better extraction guidance.

**Note:** Run this whenever the Excel definitions file is updated. The import is an optional ad-hoc step.

## 🧩  5  Running the POC

### Step 1  Ensure `.env` has API keys
Verify your `.env` file contains `SERPAPI_API_KEY` and `OPENAI_API_KEY`.

### Step 2  Activate the virtual environment
```bash
cd ~/wot-poc
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### Step 3  Run extraction
```bash
# Single project
python src/main.py --targets "cheqd"

# Multiple projects
python src/main.py --targets "cheqd,esatus,MÁS,Trusted Biz"

# All projects from input.xlsx (process all rows)
python src/main.py --targets all
```

**Options:**
- `--targets`: Comma-separated project names (or "all")
- `--provider`: `openai` (default) or `ollama`
- `--model`: Model name (default: `gpt-4o-mini`)
- `--max-output-tokens`: Max tokens for LLM response (default: 4000)
- `--with-accuracy-check`: Run accuracy check after extraction completes (optional)
- `--check-accuracy`: Run accuracy check only (skip extraction)
- `--check-coverage`: Run coverage check only (skip extraction)
- `--project`: Single project name for accuracy/coverage check (used with --check-accuracy or --check-coverage)
- `--import-codebook`: Import codebook from Excel and exit

### Step 4  Check accuracy (Optional)
Accuracy check compares AI-extracted data against manual/client data (if available).

```bash
# Method 1: Run accuracy check after extraction (using flag)
python src/main.py --targets "cheqd" --with-accuracy-check

# Method 2: Quick standalone accuracy check
python src/main.py --check-accuracy

# Method 3: Check accuracy for a single project
python src/main.py --check-accuracy --project "cheqd"

# Method 4: Auto-run after extraction (set AUTO_CHECK_ACCURACY=true in .env)
# Accuracy check automatically runs after each extraction (backward compatibility)
```

### Step 5  Check coverage
Coverage shows what percentage of fields are filled with actual data (non-empty values).

```bash
# Method 1: Check coverage for all projects
python src/main.py --check-coverage

# Method 2: Check coverage for specific projects
python src/main.py --check-coverage --project "cheqd"

# Method 3: Using the accuracy utility directly
python -m app.utils.accuracy --check-coverage --projects "cheqd,Diwala"
```

**Coverage Calculation:**
- Coverage = (filled fields / total fields) × 100
- A field is considered "filled" if it has a non-empty value
- "Failed to disclose" is a valid response but doesn't count as "filled" (it means information was not found)
- Only Data Columns are included (Product Name, ID, Logo, and source fields are excluded)

Open `data/output.xlsx` to see:
- **Input sheet**: Original data
- **AI sheet**: AI-extracted data with sources
- **Comparison sheet**: Side-by-side comparison (only Data Columns are matched)

## 📄  6  Outputs

| File | Description |
|------|-------------|
| **data/output.xlsx** | Final results with Input, AI, and Comparison sheets |
| **data/cache/{Product Name}/urls.json** | URLs found for each project |
| **data/cache/{Product Name}/texts/*.txt** | Scraped text from each page |
| **data/cache/{Product Name}/llm_raw.json** | Raw LLM response |
| **data/cache/{Product Name}/serpapi_debug.json** | SerpAPI search results (if available) |
| **logs/wot.log** | Execution logs |

## 🔍  7  How It Works

1. **Search Phase** (`searcher.py`):
   - Uses SerpAPI to find relevant URLs via Google search
   - Performs multiple targeted queries (e.g., "project name SSI", "project name about")
   - Filters and prioritizes official sources (homepage, about pages, docs)
   - Uses known websites from `input.xlsx` to improve targeting

2. **Scraping Phase** (`scraper.py`):
   - Fetches HTML from collected URLs
   - Extracts clean text content using BeautifulSoup
   - Caches scraped content for debugging

3. **Extraction Phase** (`extractor.py`):
   - Packs context from prioritized pages (homepage > about > docs > blog)
   - Uses codebook definitions to guide LLM extraction
   - Calls LLM (OpenAI or Ollama) with structured prompt
   - Extracts all fields with source evidence

4. **Export Phase** (`export_excel.py`):
   - Merges AI-extracted data with input data
   - Creates comparison sheet (only matches Data Columns, excludes source fields)
   - Uses semantic similarity matching for long text fields (60% threshold)

## 🎯  8  Accuracy Metrics

The system calculates accuracy **only for Data Columns**:
- ✅ **Included**: All data fields from `wot_data_definations.xlsx` where `extraction_needed=Y`
- ❌ **Excluded from comparison**: Product Name, ID, Logo, Live Source fields, Archived Source fields
- ℹ️ **Note**: Live Source fields ARE captured and exported - they're just not used in accuracy comparison

Text fields use semantic similarity matching (60% threshold) - meaning if two texts convey the same meaning, they're considered a match.

## 📊  9  Coverage Metrics

Coverage measures what percentage of fields are filled with actual data (non-empty values).

**Coverage Calculation:**
- Coverage = (filled fields / total fields) × 100
- A field is considered "filled" if it has a non-empty value
- "Failed to disclose" is a valid response but doesn't count as "filled" (it means information was not found)

**Fields Included:**
- ✅ All data fields from `wot_data_definations.xlsx` where `extraction_needed=Y`

**Fields Excluded:**
- ❌ Product Name, ID, Logo (internal/reference fields)
- ❌ Source columns (Live Source, Archived Source fields)
- ❌ `_evidence` (internal metadata)

**Coverage Thresholds:**
- ✅ ≥80%: Excellent coverage
- ⚠️ 60-79%: Good coverage, some fields need attention
- ❌ <60%: Low coverage, consider improving extraction strategies

## 🧰  10  Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError` | Activate venv → `pip install -r requirements.txt` |
| `SERPAPI_KEY not found` | Add `SERPAPI_API_KEY=...` to `.env` file |
| `OPENAI_API_KEY not found` | Add `OPENAI_API_KEY=...` to `.env` file |
| Low accuracy | Check SerpAPI key is active, increase `MAX_URLS_PER_PROJECT`, verify codebook imported |
| Wrong entity extracted | Ensure `Website` column in `input.xlsx` has correct URLs for known projects |

## 💡  11  Tips for Better Results

- **Known Websites**: Fill `Website` column in `input.xlsx` - helps target correct entity
- **Codebook**: Keep `data/wot_data_definations.xlsx` updated with field definitions
- **Prompt Engineering**: Edit `data/prompts.json` to tune LLM extraction instructions without touching code
- **More URLs**: Increase `MAX_URLS_PER_PROJECT` in `.env` (default: 50)
- **Context Size**: System uses top 15 pages with 8K chars each for extraction
- **Semantic Matching**: Long text fields are matched using similarity (not exact match)

### Customizing LLM Prompts

**New in v2.0**: All LLM extraction prompts are now in `data/prompts.json` for easy editing by prompt engineers without code changes.

This file contains:
- `system_prompt`: High-level LLM instructions
- `user_prompt_template`: Main extraction template with field guidance
- `field_hints`: Field-specific instructions (mission, funding, ZKP, targets, etc.)
- `type_hints`: Data type rules (URL, year, text, empty string handling)
- `allowed_failed_to_disclose_fields`: List of 6 fields that allow "Failed to disclose"
- `excluded_domains`: Domains to skip (youtube, facebook, etc.)
- `exclude_keywords`: Keywords to filter out (career, jobs, etc.)
- `fallback_hints`: Default guidance if codebook is empty

**Example**: To improve mission statement extraction, edit the `"mission"` key in `field_hints` under `data/prompts.json`.

**Important**: Changes to `prompts.json` take effect immediately on the next run - no code redeployment needed!

## 🧩  12  Folder Structure

```
wot-poc/
  ├─ data/
  │   ├─ input.xlsx                  # Input projects
  │   ├─ output.xlsx                 # Results
  │   ├─ wot_data_definations.xlsx   # Field definitions
  │   ├─ codebook.json               # Generated from Excel (auto-created)
  │   ├─ prompts.json                # LLM extraction prompts (editable by prompt engineers)
  │   └─ cache/{Product Name}/      # Cache per project (replace with actual product name)
  │       ├─ urls.json
  │       ├─ texts/*.txt
  │       └─ llm_raw.json
  ├─ logs/
  │   └─ wot.log
  ├─ src/
  │   ├─ main.py                     # Entry point
  │   └─ app/
  │       ├─ config/
  │       │   └─ codebook.py         # Codebook loader
  │       ├─ core/
  │       │   ├─ schema.py           # Schema utilities
  │       │   └─ export_excel.py     # Excel export & comparison
  │       ├─ utils/
  │       │   └─ logger.py           # Logging
  │       └─ workers/
  │           ├─ searcher.py         # SerpAPI search
  │           ├─ scraper.py          # Web scraping
  │           ├─ extractor.py        # LLM extraction
  │           └─ llm_client.py       # LLM client (OpenAI/Ollama)
  ├─ .env                            # API keys (gitignored)
  ├─ requirements.txt
  └─ README.md
```

## ✅  13  Summary

This POC automatically:
1. ✅ Searches the web for project information (SerpAPI)
2. ✅ Scrapes and extracts text from relevant pages
3. ✅ Uses LLM to extract structured data using codebook definitions
4. ✅ Exports results to Excel with source tracking
5. ✅ Compares AI results with manual data (if available)
6. ✅ Calculates accuracy metrics (Data Columns only)
7. ✅ Calculates coverage metrics (percentage of filled fields)

**Current Status**: Working on improving extraction accuracy to reach 60%+ on validation projects through prompt engineering and better source selection.
