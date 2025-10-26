# 🧠 Web of Trust POC  
### AI-Assisted Data Collection using Local Llama 3.1 (via Ollama)

This Proof of Concept automatically collects and structures data for **Digital Identity (SSI/DI)** projects in the same Excel format used by the client.  
It reads the existing `input.xlsx`, identifies rows with missing fields, scrapes public information, asks a **local AI model (Llama 3.1)** to fill the gaps, and writes results to `output.xlsx`.

---

## ⚡ Quick Start (All Steps in One Block)

Follow these steps exactly to get from zero setup → working output file.

```bash
# 1️⃣ Clone or copy the project
git clone https://github.com/sknampally/wot-poc.git
cd wot-poc

# 2️⃣ Install Python 3.12+ if not installed
# macOS
brew install python
# Windows: download from https://www.python.org/downloads/
# Linux
sudo apt install python3 python3-venv python3-pip

# 3️⃣ (Optional) Install Git if not installed
# macOS
brew install git
# Windows: https://git-scm.com/downloads
# Linux
sudo apt install git

# 4️⃣ Install Ollama and the local Llama 3.1 model
# macOS
brew install ollama
brew services start ollama
ollama pull llama3.1

# Windows
# Download from https://ollama.com/download
# Then run in PowerShell:
ollama pull llama3.1

# Linux
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1

# Verify Ollama
ollama run llama3.1 "Hello"

# 5️⃣ Create Python virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 6️⃣ Ensure .env is configured correctly
# (default should already be fine)
cat .env
# USE_OLLAMA=true
# OLLAMA_HOST=http://localhost:11434
# OLLAMA_MODEL=llama3.1
# MAX_URLS_PER_PROJECT=3
# USE_MANUAL_ONLY=true

# 7️⃣ Make sure Ollama is running
ollama serve

# 8️⃣ Place the client-provided Excel file
# Replace the existing one at:
data/input.xlsx

# 9️⃣ Run the script (auto-detects empty rows)
python src/main.py --targets auto

# 10️⃣ Open results
# See structured AI-filled output in:
data/output.xlsx
