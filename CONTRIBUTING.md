# Contributing to Chipix Studio

Thanks for your interest in contributing! This document covers the basics for getting a development environment running and submitting changes.

## Development Setup

Prerequisites: **Python 3.11**, **Node.js 20+**, **Git**. Optional: [Slang SystemVerilog CLI](https://github.com/MikePopoloski/slang), Icarus Verilog, Verilator, Yosys/SymbiYosys for stronger verification support.

```powershell
git clone https://github.com/Chipix-Company/chipix.git
cd chipix

# Backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt

# Frontend + Electron
npm install
cd frontend
npm install
cd ..
```

Copy `.env.example` to `backend/.env` and configure your LLM provider (OpenAI, Gemini, Azure OpenAI, NVIDIA NIM, or a local llama.cpp server).

To launch everything (backend, frontend, Electron):

```powershell
.\launch_all.ps1
```

## Project Layout

| Path | What lives there |
|---|---|
| `frontend/` | React + Vite desktop workspace UI |
| `backend/` | FastAPI server, verification services, agent runtime |
| `backend/agent_core/` | Event-driven agent runtime |
| `electron/` | Desktop shell and bridge |
| `RTL_designer/` | RTL designer module |
| `vscode-extension/` | VS Code extension |
| `Documentation/` | User-facing documentation |

## Running Tests

Backend (pytest):

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m pytest tests/
```

Frontend (node test scripts):

```bash
cd frontend
npm test
```

## Submitting Changes

1. Fork the repo and create a feature branch from `main`.
2. Keep changes focused — one logical change per PR.
3. Add or update tests for behavior you change.
4. Run the backend and frontend test suites before submitting.
5. Write clear commit messages describing *why*, not just *what*.
6. Open a pull request with a short description of the change and how to verify it.

## Reporting Bugs

Please use the bug report issue template and include: steps to reproduce, expected vs actual behavior, backend/frontend logs, and your OS + Python/Node versions.

## License

By contributing to Chipix Studio, you agree that your contributions will be licensed under the [GNU General Public License v3](LICENSE) that covers this project.
