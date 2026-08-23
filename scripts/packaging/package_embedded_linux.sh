#!/usr/bin/env bash
# Build Linux embedded desktop package: PyInstaller backend + React UI + Electron AppImage.
set -euo pipefail

ELECTRON_TARGET="AppImage"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      ELECTRON_TARGET="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--target AppImage|dir]" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
export REPO_ROOT

reset_stage_root() {
  local stage_root="$1"
  rm -rf "${stage_root}"
  mkdir -p "${stage_root}"
}

clear_stage_root() {
  local stage_root="$1"
  rm -rf "${stage_root}"
  mkdir -p "${stage_root}"
  touch "${stage_root}/.gitkeep"
}

echo "[1/5] Preparing isolated Python packaging environment..."
VENV_ROOT="${REPO_ROOT}/.packaging/pyinstaller-venv-linux"
VENV_PYTHON="${VENV_ROOT}/bin/python"

# Bootstrap interpreter used to create the venv. RHEL/AlmaLinux 8 ship Python 3.6 as
# `python3`, which is too old for the backend, so allow an override (e.g. python3.11).
BOOTSTRAP_PYTHON="${CHIPVERIFY_BOOTSTRAP_PYTHON:-python3}"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  "${BOOTSTRAP_PYTHON}" -m venv "${VENV_ROOT}"
fi

"${VENV_PYTHON}" -m pip install --upgrade pip
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/install_backend_pyinstaller_deps.sh"
install_backend_pyinstaller_deps "${VENV_PYTHON}" "${REPO_ROOT}/backend/requirements.txt"

echo "[2/5] Building backend ELF with PyInstaller..."
BACKEND_BUILD_ROOT="${REPO_ROOT}/backend/runtime/linux/build"
BACKEND_ROOT="${REPO_ROOT}/backend"
export PYTHON_BIN="${VENV_PYTHON}"
bash "${REPO_ROOT}/backend/runtime/linux/build_backend_elf.sh"

echo "[3/5] Staging backend runtime as Electron extraResources..."
STAGE_ROOT="${REPO_ROOT}/build-resources/runtime"
reset_stage_root "${STAGE_ROOT}"

STAGE_BACKEND="${STAGE_ROOT}/backend"
STAGE_BIN="${STAGE_ROOT}/bin"
mkdir -p "${STAGE_BACKEND}" "${STAGE_BIN}"

BACKEND_DIST_DIR="${BACKEND_BUILD_ROOT}/dist/chipverify-backend"
BACKEND_ELF="${BACKEND_DIST_DIR}/chipverify-backend"
if [[ ! -f "${BACKEND_ELF}" ]]; then
  echo "Backend ELF missing: ${BACKEND_ELF}" >&2
  exit 1
fi

cp -a "${BACKEND_DIST_DIR}/." "${STAGE_BACKEND}/"

# Stage the self-diagnosing launch wrapper next to the ELF. Electron prefers it on
# Linux so loader failures (glibc/libstdc++ mismatch on RHEL) are logged before exit.
BACKEND_LAUNCH_WRAPPER="${REPO_ROOT}/backend/runtime/linux/launch-backend.sh"
if [[ -f "${BACKEND_LAUNCH_WRAPPER}" ]]; then
  cp -f "${BACKEND_LAUNCH_WRAPPER}" "${STAGE_BACKEND}/launch-backend.sh"
  chmod +x "${STAGE_BACKEND}/launch-backend.sh"
  echo "Staged backend launch wrapper: ${STAGE_BACKEND}/launch-backend.sh"
fi

STAGED_ENV="${STAGE_BACKEND}/.env"
SOURCE_ENV="${REPO_ROOT}/backend/.env"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/stage_embedded_runtime_env.sh"
export PYTHON_BIN="${VENV_PYTHON}"
stage_embedded_runtime_env "${SOURCE_ENV}"

SOURCE_LICENSE="${REPO_ROOT}/backend/license.key"
if [[ -f "${SOURCE_LICENSE}" ]]; then
  cp -f "${SOURCE_LICENSE}" "${STAGE_BACKEND}/license.key"
fi

RUNTIME_BIN="${REPO_ROOT}/backend/runtime/bin"
if [[ -d "${RUNTIME_BIN}" ]]; then
  cp -a "${RUNTIME_BIN}/." "${STAGE_BIN}/" || true
fi

EDA_TOOLS_BIN="${REPO_ROOT}/backend/runtime/linux/eda-tools/bin"
if [[ ! -x "${EDA_TOOLS_BIN}/slang" || ! -x "${EDA_TOOLS_BIN}/svls" ]]; then
  echo "Fetching Slang + svls for TruthCore and RTL lint..."
  bash "${SCRIPT_DIR}/fetch_linux_eda_tools.sh"
fi

SLANG_SOURCE=""
if [[ -n "${CHIPVERIFY_SLANG_BIN:-}" && -x "${CHIPVERIFY_SLANG_BIN}" ]]; then
  SLANG_SOURCE="${CHIPVERIFY_SLANG_BIN}"
elif [[ -x "${EDA_TOOLS_BIN}/slang" ]]; then
  SLANG_SOURCE="${EDA_TOOLS_BIN}/slang"
elif command -v slang >/dev/null 2>&1; then
  SLANG_SOURCE="$(command -v slang)"
fi
if [[ -n "${SLANG_SOURCE}" ]]; then
  cp -f "${SLANG_SOURCE}" "${STAGE_BIN}/slang"
  chmod +x "${STAGE_BIN}/slang"
  echo "Bundled Slang parser: ${SLANG_SOURCE}"
  "${STAGE_BIN}/slang" --version | head -1 || true
else
  echo "Warning: Slang parser not found. TruthCore may require CHIPVERIFY_SLANG_BIN on target." >&2
fi

SVLS_SOURCE=""
if [[ -n "${CHIPVERIFY_SVLS_BIN:-}" && -x "${CHIPVERIFY_SVLS_BIN}" ]]; then
  SVLS_SOURCE="${CHIPVERIFY_SVLS_BIN}"
elif [[ -x "${EDA_TOOLS_BIN}/svls" ]]; then
  SVLS_SOURCE="${EDA_TOOLS_BIN}/svls"
elif command -v svls >/dev/null 2>&1; then
  SVLS_SOURCE="$(command -v svls)"
fi
if [[ -n "${SVLS_SOURCE}" ]]; then
  cp -f "${SVLS_SOURCE}" "${STAGE_BIN}/svls"
  chmod +x "${STAGE_BIN}/svls"
  echo "Bundled svls linter: ${SVLS_SOURCE}"
  "${STAGE_BIN}/svls" --version | head -1 || true
else
  echo "Warning: svls not found. RTL lint may be skipped on target." >&2
fi

cat > "${STAGE_ROOT}/runtime-manifest.json" <<EOF
{
  "product": "chipverify-desktop",
  "created_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "backend_runtime": "runtime/backend/chipverify-backend",
  "slang_runtime": "runtime/bin/slang",
  "slang_bundled": $([[ -n "${SLANG_SOURCE}" ]] && echo true || echo false),
  "svls_runtime": "runtime/bin/svls",
  "svls_bundled": $([[ -n "${SVLS_SOURCE}" ]] && echo true || echo false),
  "embedded_env": true,
  "activation_required": true,
  "electron_target": "${ELECTRON_TARGET}",
  "platform": "linux"
}
EOF

echo "[4/5] Building frontend..."
echo "Installing frontend dependencies..."
(cd "${REPO_ROOT}/frontend" && npm ci)
VERSION="$(node -p "require('./package.json').version")"
export CHIPVERIFY_PACKAGE_VERSION="${VERSION}"
export CHIPVERIFY_SENTRY_RELEASE="chipverify-desktop@${VERSION}"
export VITE_SENTRY_DSN="${CHIPVERIFY_SENTRY_DSN:-}"
export VITE_SENTRY_ENVIRONMENT="${CHIPVERIFY_SENTRY_ENVIRONMENT:-production}"
export VITE_SENTRY_RELEASE="chipverify-desktop@${VERSION}"
export VITE_APP_VERSION="${VERSION}"
npm run build:frontend

echo "[5/5] Building Electron Linux package with embedded backend..."
npx electron-builder --linux "${ELECTRON_TARGET}" --x64 --publish never

UNPACKED_BACKEND="${REPO_ROOT}/dist-electron/linux-unpacked/resources/runtime/backend/chipverify-backend"
if [[ -f "${UNPACKED_BACKEND}" ]]; then
  chmod +x "${UNPACKED_BACKEND}"
  echo "Running packaged self-test on embedded backend (services.verification.*, graphify, routes)..."
  "${UNPACKED_BACKEND}" --self-test-packaged
  echo "EMBEDDED_BACKEND=${UNPACKED_BACKEND}"
  echo "EMBEDDED_BACKEND_SELF_TEST=ok"
else
  echo "Warning: Could not verify linux-unpacked embedded backend at '${UNPACKED_BACKEND}'." >&2
fi

for pattern in '*.rpm' '*linux-x64*.AppImage' '*.AppImage'; do
  ARTIFACT="$(find "${REPO_ROOT}/dist-electron" -maxdepth 1 -name "${pattern}" -type f 2>/dev/null | head -n 1 || true)"
  if [[ -n "${ARTIFACT}" ]]; then
    echo "ARTIFACT=${ARTIFACT}"
  fi
done

clear_stage_root "${STAGE_ROOT}"
