# Chipix Studio

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)
![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

**The world's first open-source, AI-native studio for chip design and verification.**

A desktop workspace where you bring a specification and RTL, and an AI agent helps you understand the design, plan verification, generate UVM environments, formal properties, and unit simulations — then runs them and collects evidence that the design actually works.

## Why we exist

If you want to build software today, your tools are free. GCC, LLVM, Linux, VS Code, Git — the entire stack is open, and nobody questions it.

If you want to build and verify a chip, the story is different. The industry's design and verification tooling sits behind licenses from a handful of vendors that cost tens of thousands of dollars per seat, every year. A student can't afford a verification environment. A two-person startup burns its runway before taping out. Even large companies restrict tool access to the people whose budget covers it.

We think that's backwards. Access to good tools shouldn't depend on the size of your purchase order. Every engineer, at every company, at every stage — should be able to sit down and verify a design. That's why Chipix is open source, and why it will stay open source. You can inspect it, modify it, self-host it, and build a business on it. No seat counting, no lock-in, no black boxes between you and your own verification results.

And because verification is slow, repetitive, and expert-heavy, we're putting an AI agent inside the loop — one you control, running against models you choose, including models that never leave your machine.

## What Chipix is

Chipix Studio is a cross-platform desktop application (Windows and Linux) with three parts:

- **A React workspace UI** in an Electron shell — a thread-first interface where conversations, files, plans, and results live side by side.
- **A FastAPI backend** with services for spec ingestion, mental-model building, staged verification, simulation adapters, and compile gates.
- **An agent runtime** (`agent_core`) that drives tool calls, streams events to the UI, and keeps humans in charge of what actually executes.

It works with the open EDA tools you already trust — Icarus Verilog, Verilator, Yosys/SymbiYosys, Slang — instead of replacing them.

## What we're building

The direction is simple: make the full path from *specification* to *verified design* something anyone can walk, end to end, for free.

Right now that means:

- A **mental model** of your design, built from your spec and RTL, that persists as shared memory for every downstream agent step.
- **Staged verification**: recommend a strategy, plan it, show the plan to a human, then execute UnitSim, Formal, or UVM flows on approval.
- **Evidence traceability**: every generated artifact traces back to requirements and writes results back as evidence.
- Next: stronger formal property generation, regression intelligence that predicts which tests will fail, testbenches that adapt when RTL changes, and multi-project team features.

## What it does

**Verification, planned by AI and approved by you**

- Ingests specs (PDF, DOCX, images, text) and RTL projects, then builds a source-grounded mental model with readiness checks.
- Recommends a verification strategy and generates reviewable plans for unit simulation, formal property verification (SVA), and UVM environments.
- Executes through real simulators via adapter plugins, with validation and compile gates before anything is accepted.
- Stores validation results, coverage, and compile-gate outcomes as evidence tied to requirements.

**A workspace built around the work**

- Thread-first chat with streaming responses, clarifying questions, patches, diffs, and a task board.
- IDE-style file editing with inline completion, SystemVerilog linting via `svls`, and an RTL visualizer.
- Codebase graph overlay so agents (and you) can see how modules connect.
- Onboarding tours, keyboard shortcuts, and a command palette.

**Runs where you need it**

- Desktop app on Windows and Linux, packaged installers included.
- Bring your own LLM: Gemini, OpenAI, Azure OpenAI, NVIDIA NIM, OpenAI-compatible endpoints, or a fully local llama.cpp GGUF model — nothing leaves your machine unless you say so.
- VS Code extension for TruthCore-backed verification without leaving your editor.

## How it helps you

**If you're a startup:** stand up a real verification flow without signing an enterprise contract. Your seed money goes to engineers, not seats.

**If you're a student or researcher:** everything the professionals use, on your laptop, with local models if you have no API budget. Learn UVM and formal by reading what the agent generates and why.

**If you're an established team:** automate the boring parts of verification while keeping engineers in approval loops. Self-host it, audit it, extend it — the GPL guarantees the code stays yours to inspect.

**If you build EDA tooling:** Chipix is an open integration surface. Our simulator adapters, parser hooks, and agent tool APIs are meant to be plugged into.

## Quick start

```powershell
git clone https://github.com/Chipix-Company/chipix.git
cd chipix
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
npm install && cd frontend && npm install && cd ..
.\launch_all.ps1
```

Full setup instructions — LLM provider configuration, testing, packaging, releases, troubleshooting — are in [DEVELOPMENT.md](DEVELOPMENT.md).

## Repository layout

| Path | Purpose |
|---|---|
| `frontend/` | React + Vite desktop workspace UI |
| `backend/` | FastAPI server, project/artifact APIs, staged verification routes |
| `backend/services/mental_model/` | Mental-model builder, schema, storage, readiness checks |
| `backend/services/verification/` | Strategy recommendation, UnitSim/Formal/UVM planning and generation |
| `backend/agent_core/` | Event-driven agent runtime |
| `backend/agent_tools/` | Tool wrappers used by the agent system |
| `RTL_designer/` | RTL designer module |
| `vscode-extension/` | VS Code extension |
| `electron/` | Electron shell and desktop bridge |
| `Documentation/` | User-facing guides |
| `scripts/packaging/` | Windows and Linux packaging helpers |

## Community

- [Contributing guide](CONTRIBUTING.md) — setup, tests, and how to submit PRs
- [Development guide](DEVELOPMENT.md) — configuration, packaging, releases
- [Security policy](SECURITY.md) — how to report vulnerabilities privately
- [Agent architecture](AGENTS.md) — how the agent runtime works

## License

Copyright © 2026 Chipix Company.

This project is licensed under the [GNU General Public License v3.0](LICENSE)
(SPIDX: `GPL-3.0-or-later`). Any redistribution or modification of this
software must comply with the terms of the GPL-3.0 license.
