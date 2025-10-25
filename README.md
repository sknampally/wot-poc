# 🧠 Web of Trust POC  
### AI-Assisted Data Collection using Local Llama 3.1 (via Ollama)

This Proof of Concept automatically collects and structures data for **Digital Identity (SSI/DI)** projects in the same Excel format used by the client.  
It reads the existing `input.xlsx`, identifies rows with missing fields, scrapes public information, asks a **local AI model (Llama 3.1)** to fill the gaps, and writes results to `output.xlsx`.

---

## 🚀 1  Prerequisites — Install Once

### A  Install Python 3.12 or newer
| Platform | Command / Download |
|-----------|--------------------|
| **macOS** | `brew install python`  or  [python.org/downloads](https://www.python.org/downloads/) |
| **Windows** | [Download Installer](https://www.python.org/downloads/windows/) → check ✅ *Add Python to PATH* |
| **Linux** | `sudo apt install python3 python3-venv python3-pip` |

Verify:
```bash
python3 --version

## 🚀 2  Install Git (optional)
If not installed:
macOS: brew install git
Windows: git-scm.com/downloads

C Install Ollama + Llama 3.1 (local model)

Ollama runs LLMs locally — no OpenAI keys or cloud fees.

macOS
brew install ollama
brew services start ollama        # run as background service
ollama pull llama3.1              # downloads ~4 GB model

Windows

Download & install → ollama.com/download

Start Ollama from the Start Menu (it runs as a service)

In PowerShell:

ollama pull llama3.1

Linux
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1


Verify installation:

ollama run llama3.1 "Hello"


If you get a reply, the model is ready.

🗂 2 Project Setup
A Get the project folder

Download or clone the wot-poc folder (for example to ~/wot-poc).

wot-poc/
  data/
    input.xlsx        # client-provided workbook
  src/                # Python scripts
  README.md
  requirements.txt
  .env

B Create and activate a Python environment
cd ~/wot-poc
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

⚙️ 3 Configure the environment

Your .env file (included) should look like:

USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1
MAX_URLS_PER_PROJECT=3
USE_MANUAL_ONLY=true


Tip:
To let the script find URLs automatically, set
USE_MANUAL_ONLY=false.

🧩 4 Running the POC
Step 1 Start Ollama
ollama serve


(or brew services start ollama on macOS)

Step 2 Activate the virtual environment
cd ~/wot-poc
source .venv/bin/activate

Step 3 Ensure data/input.xlsx is the client file

It should contain four filled sample rows and several blank rows for projects to be auto-filled.

Step 4 Run the POC
python src/main.py --targets auto


What happens:

The script reads input.xlsx and detects rows that have names but mostly empty fields.

For each project name, it scrapes or loads URLs (or reads manual_urls.txt if provided).

It asks Llama 3.1 to extract structured data.

It merges results into data/output.xlsx, keeping existing data intact.

It adds a Review sheet listing evidence URLs and validation notes.

🌐 5 (Recommended) Add Manual URLs

You can guide the model by supplying 2-4 official sources per project:

mkdir -p data/cache/Your_Project_Name
nano data/cache/Your_Project_Name/manual_urls.txt


Each line = one URL (e.g., official site, press release, GitHub repo).

When USE_MANUAL_ONLY=true, the script uses only these URLs — no online search.

📄 6 Outputs
File	Description
data/output.xlsx	Final results merged into the original structure
Review sheet	Field values, source URLs, and validation issues
data/cache/<Project>/record_debug.json	Full AI JSON response
data/cache/<Project>/texts/*.json	Scraped text from each page
🧰 7 Troubleshooting
Issue	Solution
ModuleNotFoundError	Activate the venv → pip install -r requirements.txt
could not connect to localhost:11434	Start Ollama: ollama serve
First run very slow	Normal — model loads into memory (1-2 min)
Output blank	Add manual URLs or set USE_MANUAL_ONLY=true
Mac shows Xcode Python path	Use python3 instead of python
🧾 8 Example Run Log
$ ollama serve
$ source .venv/bin/activate
$ python src/main.py --targets auto

[auto] detected targets: ['Mina', 'eIDAS Bridge', 'Verida', 'ID2020']
Processing Mina
Processing eIDAS Bridge
Processing Verida
Processing ID2020
Done → /Users/<user>/wot-poc/data/output.xlsx


Open data/output.xlsx → check both the main sheet and Review sheet.

💡 9 Tips for Better Results

Always include a few good manual URLs for each blank project.

Increase MAX_URLS_PER_PROJECT to 6–8 for richer context.

Once stable, raise snippet length in extractor.py for deeper extractions.

For debugging, open data/cache/<Project>/record_debug.json.

🧩 10 Folder Structure
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
