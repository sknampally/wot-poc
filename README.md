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

B Install Git (optional)

| Platform    | Command / Link                                         |
| ----------- | ------------------------------------------------------ |
| **macOS**   | `brew install git`                                     |
| **Windows** | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **Linux**   | `sudo apt install git`                                 |

C Install Ollama + Llama 3.1 (Local Model)

Ollama runs LLMs locally — no API keys or cloud costs.

macOS
brew install ollama
brew services start ollama        # start background service
ollama pull llama3.1              # ~4 GB download

Windows

Download → ollama.com/download
Start Ollama from the Start Menu (it runs as a service).
Then in PowerShell:

ollama pull llama3.1

Verify:
ollama run llama3.1 "Hello"


