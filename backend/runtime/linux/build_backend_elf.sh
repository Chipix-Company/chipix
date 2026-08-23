#!/usr/bin/env bash
# Build chipverify-backend as a PyInstaller --onedir ELF bundle for Linux.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="${BACKEND_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-${CHIPVERIFY_PYTHON_BIN:-python3}}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SCRIPT_DIR}/build}"

SERVICE_ENTRYPOINT="${BACKEND_ROOT}/runtime/linux/backend_service_entrypoint.py"
LEGACY_CORE_ROOT="${BACKEND_ROOT}/original_core"
ENV_DEFAULTS="${BACKEND_ROOT}/runtime/linux/.env.defaults"

if [[ ! -f "${SERVICE_ENTRYPOINT}" ]]; then
  echo "Backend service entrypoint not found: ${SERVICE_ENTRYPOINT}" >&2
  exit 1
fi

if [[ ! -d "${LEGACY_CORE_ROOT}" ]]; then
  echo "Legacy core import root not found: ${LEGACY_CORE_ROOT}" >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python not found: ${PYTHON_BIN}. Set PYTHON_BIN or CHIPVERIFY_PYTHON_BIN." >&2
  exit 1
fi

mkdir -p "${OUTPUT_ROOT}"
DIST_PATH="${OUTPUT_ROOT}/dist"
WORK_PATH="${OUTPUT_ROOT}/work"

echo "Installing/ensuring PyInstaller is available..."
"${PYTHON_BIN}" -m pip install --upgrade pyinstaller

PYINSTALLER_ARGS=(
  --noconfirm
  --clean
  --onedir
  --name chipverify-backend
  --distpath "${DIST_PATH}"
  --workpath "${WORK_PATH}"
  --specpath "${OUTPUT_ROOT}"
  --additional-hooks-dir "${SCRIPT_DIR}/pyinstaller_hooks"
  --paths "${BACKEND_ROOT}"
  --paths "${LEGACY_CORE_ROOT}"
  --collect-all pydantic_core
  --collect-all passlib
  --collect-all jose
  --collect-all pyverilog
  --collect-all certifi
  --collect-all sqlalchemy
  --collect-all tree_sitter
  --collect-submodules original_core
  --collect-submodules core
  --collect-submodules agents
  --collect-submodules parsers
  --collect-submodules routes
  --collect-submodules database
  --collect-submodules services
  --collect-submodules services.verification
  --collect-submodules observability
  --collect-submodules sentry_sdk
  --collect-submodules agent_tools
  --collect-submodules graphify
  --collect-submodules simulator_plugins
  --hidden-import simulator_plugins.cadence_integration_fixture
  --hidden-import simulator_plugins.xcelium_runner
  --hidden-import services.verification.uvm_gen
  --hidden-import services.verification.test_plan
  --hidden-import services.verification.formal_gen
  --hidden-import services.verification.uvm_planner
  --hidden-import services.verification.uvm_validator
  --hidden-import main
  --hidden-import config
  --hidden-import uvicorn.logging
  --hidden-import uvicorn.lifespan.on
  --hidden-import uvicorn.loops.auto
  --hidden-import uvicorn.protocols.http.auto
  --hidden-import uvicorn.protocols.websockets.auto
  --hidden-import uvicorn.protocols.http.h11_impl
  --hidden-import uvicorn.protocols.http.httptools_impl
  --hidden-import uvicorn.protocols.websockets.wsproto_impl
  --hidden-import uvicorn.protocols.websockets.websockets_impl
)

if "${PYTHON_BIN}" -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('tree_sitter_verilog') else 1)" 2>/dev/null; then
  PYINSTALLER_ARGS+=(--collect-all tree_sitter_verilog)
fi

if [[ -f "${ENV_DEFAULTS}" ]]; then
  PYINSTALLER_ARGS+=(--add-data "${ENV_DEFAULTS}:.env.defaults")
fi

# Bundle the FIFO benchmark RTL used by the Cadence integration smoke test
# (Apply & Test). The code has an embedded fallback, but shipping the real file
# keeps the fixture identical to development and avoids drift.
FIFO_FIXTURE_DIR="${BACKEND_ROOT}/benchmarks/fifo"
if [[ -d "${FIFO_FIXTURE_DIR}" ]]; then
  PYINSTALLER_ARGS+=(--add-data "${FIFO_FIXTURE_DIR}:benchmarks/fifo")
fi

PYINSTALLER_ARGS+=("${SERVICE_ENTRYPOINT}")

pushd "${BACKEND_ROOT}" >/dev/null
echo "Building backend ELF bundle..."
"${PYTHON_BIN}" -c "import sys; sys.path[:0]=['${BACKEND_ROOT}','${LEGACY_CORE_ROOT}']; import services.verification.uvm_gen; print('preflight: services.verification.uvm_gen ok')"
"${PYTHON_BIN}" -m PyInstaller "${PYINSTALLER_ARGS[@]}"
popd >/dev/null

ELF_PATH="${DIST_PATH}/chipverify-backend/chipverify-backend"
if [[ ! -f "${ELF_PATH}" ]]; then
  echo "Build finished but ELF not found at: ${ELF_PATH}" >&2
  exit 1
fi

chmod +x "${ELF_PATH}"
# Fail the build if verification/graphify modules (services.*, routes.*, etc.) were not
# bundled — catches POST /verification/plan ImportError before shipping AppImage/RPM.
"${ELF_PATH}" --self-test-packaged
echo "BUILT_ELF=${ELF_PATH}"
echo "Run with required env vars (CHIPVERIFY_SECRET_KEY, CHIPVERIFY_BACKEND_PORT, etc.) before starting."
