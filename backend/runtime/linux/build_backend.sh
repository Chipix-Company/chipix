#!/usr/bin/env bash
# Red Hat / generic Linux PyInstaller backend build — delegates to build_backend_elf.sh
# so collect-submodules (services.verification.*, routes, agent_tools, etc.) stay in sync.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BACKEND_ROOT="${1:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
export OUTPUT_ROOT="${2:-${SCRIPT_DIR}/build}"
export PYTHON_BIN="${CHIPVERIFY_PYTHON_BIN:-python3}"

exec bash "${SCRIPT_DIR}/build_backend_elf.sh"
