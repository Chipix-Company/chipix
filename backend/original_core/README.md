# ⚡ ChipVerify AI — AGI Verification Platform

> Powered by **Gemini 2.5 Pro** · Full UVM Testbench Generation · Formal SVA · Self-Healing · Knowledge Graph

---

## 🚀 Quick Start (After Unzipping)

### Step 1 — Prerequisites

Make sure you have **Python 3.10 or newer** installed.  
Check by opening a terminal / PowerShell and running:

```bash
python --version
```

If Python is not installed, download it from https://python.org/downloads  
✅ **Check "Add Python to PATH"** during installation.

---

### Step 2 — Open in VS Code

1. Unzip the folder (e.g., `chipverify_ai/`)
2. Open **VS Code**
3. Click **File → Open Folder** and select the `chipverify_ai/` folder
4. Open the built-in terminal: **Terminal → New Terminal** (or press `` Ctrl+` ``)

---

### Step 3 — Create a Virtual Environment

In the VS Code terminal, run:

```bash
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

You should see `(.venv)` appear at the start of your terminal prompt.

---

### Step 4 — Install Dependencies

```bash
pip install -r requirements.txt
```

This installs Streamlit, Gemini SDK, PyMuPDF, and all other required packages (~2–3 minutes).

---

### Step 5 — Set Your Gemini API Key

You need a **Gemini API key** from Google AI Studio:  
👉 https://aistudio.google.com/app/apikey

Set it as an environment variable:

```bash
# Windows (PowerShell) — lasts for this session only
$env:GOOGLE_API_KEY = "your-api-key-here"

# Windows (permanent — set in System Environment Variables)
# Search "Environment Variables" in Start Menu → Add GOOGLE_API_KEY

# macOS / Linux
export GOOGLE_API_KEY="your-api-key-here"
```

Or create a `.env` file in the `chipverify_ai/` folder:

```
GOOGLE_API_KEY=your-api-key-here
```

---

### Step 6 — Run the App

```bash
python -m streamlit run app.py
```

The app will open automatically at **http://localhost:8501** in your browser.

---

## 🖥️ Using the App

| Step | What to do |
|------|-----------|
| 1 | Upload your **RTL file** (`.v` or `.sv`) or enter a project directory path |
| 2 | Upload your **Specification** (`.txt`, `.pdf`, or `.md`) |
| 3 | Choose output directory (default: `output/`) |
| 4 | Toggle **UVM Mode** ON for full 18-file UVM testbench |
| 5 | Click **🚀 Run Verification Pipeline** |

### Tabs explained:
| Tab | Description |
|-----|-------------|
| 📟 Console | Real-time pipeline log |
| 🏗️ Hierarchy | RTL design viewer |
| 🧠 Reasoning | AI thought process (THINK → ACT → OBSERVE) |
| ⚖️ Debate | Generator → Critic → Judge refinement |
| 📋 Plan | Test scenarios, assertions, coverage plan |
| 🔬 Knowledge | Mental model — ports, signals, modules |
| ⚗️ Formal | SVA properties, Tcl scripts, formal results |
| 🔍 Review | RTL lint & design review |
| 💬 Chat | AI assistant — chat or edit files |
| 📂 Files | All generated verification files |

---

## 📁 Output Structure

After running, all outputs appear in `output/`:

```
output/
├── verification/        ← Generated UVM .sv files (18 files)
├── formal/              ← SVA bind module + JasperGold/VC Formal Tcl
├── reports/             ← JSON analysis reports + symbol table
├── rtl/                 ← Copy of your RTL
└── knowledge/           ← Persistent knowledge graph (cross-run learning)
```

---

## ⚠️ Common Issues

### ❌ `ModuleNotFoundError: No module named 'streamlit'`
Run `pip install -r requirements.txt` again with the virtual environment active.

### ❌ `GOOGLE_API_KEY not set` or authentication error
Set your API key as described in Step 5 above.

### ❌ Port 8501 already in use
Run on a different port:
```bash
python -m streamlit run app.py --server.port 8502
```

### ❌ PowerShell says `.venv\Scripts\activate` is not recognized
Run this first to allow scripts:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## 🔧 VS Code Extensions (Recommended)

Install these for the best experience:
- **Python** (Microsoft) — syntax highlighting, IntelliSense
- **Pylance** — type checking
- **Verilog-HDL/SystemVerilog** — RTL file highlighting

---

## 📋 Requirements

- Python ≥ 3.10
- Internet connection (for Gemini API calls)
- Google Gemini API key (free tier available)
- Optional: JasperGold or VC Formal on PATH for formal verification execution

---

## 🏗️ Architecture

```
chipverify_ai/
├── app.py                  ← Streamlit UI (10 tabs)
├── config.py               ← Model & path configuration
├── requirements.txt        ← Python dependencies
├── core/
│   ├── orchestrator.py     ← Main 6-phase pipeline controller
│   ├── knowledge_graph.py  ← Persistent cross-run learning
│   ├── self_healer.py      ← Auto-adapt TB on RTL changes
│   ├── parallel_agents.py  ← Parallel spec+RTL parsing
│   ├── hallucination_guard.py ← Anti-hallucination validator
│   └── ...
├── agents/
│   ├── formal_agent.py     ← SVA + JasperGold/VC Formal
│   ├── uvm_generator.py    ← 18-file UVM environment
│   ├── uvm_updater.py      ← Delta updates to existing UVM
│   └── ...
└── parsers/
    ├── pdf_parser.py       ← PDF spec extraction
    └── rtl_project.py      ← Multi-file RTL project scanner
```

---

*ChipVerify AI v2.0 — Built with Gemini 2.5 Pro, Streamlit, and UVM 1.2 / IEEE 1800.2*
