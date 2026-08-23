# 🔧 RTL Design Agent

> AI-Powered RTL Code Generation Tool — Built with LangChain, LangGraph & Google Gemini

An intelligent agent that generates production-quality Verilog/SystemVerilog code from natural language prompts. Like Cursor for hardware design — describe what you want, and the agent plans, codes, reviews, and auto-creates all project files.

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key
set GOOGLE_API_KEY=your-key-here        # Windows
export GOOGLE_API_KEY=your-key-here     # Linux/Mac

# 3. Launch the app
streamlit run app.py
```

Open **http://localhost:8501**, paste your Gemini API key in the sidebar, and type a design prompt.

---

## 🧠 How It Works — Agent Pipeline

The tool uses a **multi-agent pipeline** built on LangGraph's StateGraph. The flow adapts automatically based on design complexity.

### Simple Designs (< 500 lines)

```
User Prompt → Planner → Coder → Reviewer → File Writer → Visualizer
```

### Large Designs (500+ lines) — Automatic Hierarchical Decomposition

```
User Prompt → Planner → Decomposer → Coder (loops per submodule) → Composer → Reviewer → File Writer → Visualizer
```

| Agent | Role |
|-------|------|
| **📋 Planner** | Analyzes your prompt, extracts module specs (ports, parameters, behavior), estimates complexity |
| **🔀 Decomposer** | For large designs: breaks spec into independent submodules, each under ~400 lines |
| **💻 Coder** | Generates synthesizable RTL code. For large designs, generates one submodule per LLM call |
| **🔗 Composer** | Integrates all submodules into one clean file, fixes interface mismatches |
| **🔍 Reviewer** | Checks code for correctness, synthesizability, latch prevention, naming conventions. Can send code back to Coder (up to 3 revision cycles) |
| **📁 File Writer** | Auto-creates project folder with individual module files, testbenches, README |
| **📐 Visualizer** | Renders interactive D3.js block diagrams showing ports, signals, submodules |

---

## 📁 Automatic File Generation

When the pipeline completes, the **File Writer** automatically creates a full project structure on disk. No manual saving needed.

### What Gets Created

```
output/<module_name>/
│
├── rtl/                          # RTL Source Files
│   ├── counter.v                 # ← Each module = its own file
│   ├── alu.v                     # ← Separate file per module
│   ├── top_design.v              # ← Top-level module (separate)
│   └── <project>_top.v           # ← Combined file (all modules in one)
│
├── tb/                           # Testbench Stubs
│   ├── tb_counter.v              # ← Auto-generated testbench for counter
│   ├── tb_alu.v                  # ← Auto-generated testbench for alu
│   └── tb_top_design.v           # ← Auto-generated testbench for top
│
├── docs/                         # Documentation (user-editable)
├── sim/                          # Simulation scripts (user-editable)
│
├── README.md                     # ← Auto-generated project docs
└── project.json                  # ← Manifest with file metadata
```

### How It Works Step-by-Step

1. **Module Extraction** — The generated RTL code is parsed to find all `module...endmodule` blocks
2. **Individual Files** — Each module is written to its own `.v` (Verilog) or `.sv` (SystemVerilog) file in `rtl/`
3. **Combined File** — All modules are also written to a single `<project>_top.v` file for convenience
4. **Testbench Stubs** — For each module, a basic testbench is auto-generated in `tb/` with:
   - Clock generation (100MHz)
   - Reset sequence
   - DUT instantiation template
   - VCD waveform dump setup
5. **README.md** — Documents the project: file list, line counts, original specification
6. **project.json** — Machine-readable manifest with timestamps, file sizes, and metadata
7. **Sidebar File Tree** — The Streamlit UI shows a live file tree of everything that was created

### Output Directory

By default, files are saved to `output/` inside the project folder. You can change the output path in the sidebar under **📂 Output Directory**.

---

## 🏗️ Handling Large Designs (2000+ Lines)

LLMs have output token limits, so the agent uses **hierarchical decomposition** for large designs:

1. **Planner** estimates complexity: `SMALL | MEDIUM | LARGE | VERY_LARGE`
2. If `LARGE` or `VERY_LARGE`:
   - **Decomposer** breaks the design into submodules (~400 lines each)
   - **Coder** generates each submodule in a separate LLM call, with context about sibling modules for port matching
   - **Composer** merges all submodules into one clean file
3. Each submodule gets its own file in `rtl/` + its own testbench in `tb/`

### Example: RISC-V Core (2000+ lines)

```
Prompt: "Design a simple 5-stage RISC-V RV32I pipeline"

→ Decomposed into:
  1. instruction_fetch.v      (~200 lines)
  2. instruction_decode.v     (~300 lines)
  3. execute_unit.v           (~350 lines)
  4. memory_stage.v           (~150 lines)
  5. writeback_stage.v        (~100 lines)
  6. register_file.v          (~80 lines)
  7. hazard_unit.v            (~200 lines)
  8. riscv_pipeline_top.v     (~250 lines)  ← top-level
```

---

## 📐 Design Visualizer

After code generation, the tool renders **interactive block diagrams** using D3.js:

- **Module box** with colored port pins:
  - 🟢 Green = Input
  - 🔴 Red = Output
  - 🟡 Yellow = Inout
- **Internal signals** listed inside the module
- **Submodule instances** shown as nested blocks
- **Always blocks** indicators (sequential ⏱ / combinational ⚡)
- **Zoom & Pan** support
- **Tooltips** on hover showing port details (width, direction, type)

---

## 📂 Project Structure

```
RTl_designer/
├── app.py                    # Streamlit chat UI
├── requirements.txt          # Python dependencies
├── .env.example              # API key template
│
├── agents/                   # LangGraph agent pipeline
│   ├── state.py              # Shared state (AgentState TypedDict)
│   ├── prompts.py            # System prompts for all agents
│   ├── nodes.py              # Agent node functions
│   └── graph.py              # StateGraph definition + routing
│
├── utils/                    # Utilities
│   ├── helpers.py            # Code extraction, filename generation
│   └── file_manager.py       # Auto file creation engine
│
└── visualizer/               # Block diagram renderer
    ├── parser.py             # Verilog/SV parser (regex-based)
    └── renderer.py           # D3.js HTML generator
```

---

## ⚙️ Configuration

| Setting | Where | Default |
|---------|-------|---------|
| API Key | Sidebar or `GOOGLE_API_KEY` env var | — |
| HDL Language | Sidebar dropdown | Verilog |
| Gemini Model | Sidebar dropdown | gemini-2.5-pro |
| Output Directory | Sidebar text input | `./output` |

---

## 📝 Example Prompts

```
"Design a 4-bit synchronous counter with async reset"
"Create an AXI4-Lite slave interface with 4 registers"
"Design an 8-bit UART transmitter with configurable baud rate"
"Build a 4-stage pipelined multiplier for 16-bit operands"
"Design a simple SPI master controller"
```
