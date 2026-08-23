#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET="${1:-all}"

case "${TARGET}" in
  all|AppImage|rpm) ;;
  *)
    echo "Usage: $0 [all|AppImage|rpm]" >&2
    exit 2
    ;;
esac

if [[ ! -f /etc/os-release ]]; then
  echo "This script must be run inside a Linux builder." >&2
  exit 2
fi

# Build on a RHEL-compatible base so the packaged PyInstaller backend starts on
# Red Hat / Rocky / Alma / Oracle Linux machines with older glibc.
source /etc/os-release
if [[ "${ID_LIKE:-} ${ID:-}" != *"rhel"* && "${ID_LIKE:-} ${ID:-}" != *"centos"* && "${ID_LIKE:-} ${ID:-}" != *"fedora"* ]]; then
  echo "Red Hat package must be built on a RHEL-family distro; current ID=${ID:-unknown} ID_LIKE=${ID_LIKE:-unknown}." >&2
  exit 2
fi

cd "${REPO_ROOT}"

PYTHON_BIN="${CHIPVERIFY_PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
  elif command -v python3.10 >/dev/null 2>&1; then
    PYTHON_BIN="python3.10"
  else
    PYTHON_BIN=""
  fi
fi

if [[ -z "${PYTHON_BIN}" ]] || ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python 3.10+ is required for Red Hat packaging because backend dependencies such as tree-sitter require it. Install python3.11/python3.11-devel and rerun." >&2
  exit 1
fi
PYTHON_VERSION_MAJOR_MINOR="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "${PYTHON_VERSION_MAJOR_MINOR}" in
  3.10|3.11|3.12|3.13|3.14) ;;
  *)
    echo "Python 3.10+ is required for Red Hat packaging; found ${PYTHON_VERSION_MAJOR_MINOR} from ${PYTHON_BIN}." >&2
    exit 1
    ;;
esac
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required. Install nodejs:20+ or nodejs:22 and rerun." >&2
  exit 1
fi
if [[ "${TARGET}" != "AppImage" ]] && ! command -v rpmbuild >/dev/null 2>&1; then
  echo "rpmbuild is required for RPM output. Install rpm-build and rerun, or use: $0 AppImage" >&2
  exit 1
fi

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [[ "${NODE_MAJOR}" -lt 20 ]]; then
  echo "Node.js 20+ is required; found $(node -v)." >&2
  exit 1
fi

LDD_VERSION_OUTPUT="$(ldd --version 2>&1 || true)"
GLIBC_VERSION="$(printf '%s\n' "${LDD_VERSION_OUTPUT}" | sed -n '1s/.*\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')"
GLIBC_VERSION="${GLIBC_VERSION:-unknown}"
NODE_VERSION="$(node -v)"
PYTHON_VERSION="$("${PYTHON_BIN}" --version 2>&1)"
echo "Red Hat builder: ${PRETTY_NAME:-${ID}} | glibc ${GLIBC_VERSION} | node ${NODE_VERSION} | ${PYTHON_VERSION}"

VENV_ROOT="${REPO_ROOT}/.packaging/pyinstaller-redhat-venv"
VENV_PYTHON="${VENV_ROOT}/bin/python"
PACKAGING_REQUIREMENTS="${REPO_ROOT}/.packaging/requirements-redhat.txt"
BACKEND_BUILD_ROOT="${REPO_ROOT}/backend/runtime/redhat/build"
STAGE_ROOT="${REPO_ROOT}/build-resources/runtime"
STAGE_BACKEND="${STAGE_ROOT}/backend"
STAGE_BIN="${STAGE_ROOT}/bin"
DIST_REDhat="${REPO_ROOT}/dist-redhat"
GLIBC_COMPAT_SCRIPT="${REPO_ROOT}/scripts/packaging/check_linux_glibc_compat.sh"
REDHAT_GLIBC_MAX="${CHIPVERIFY_REDHAT_GLIBC_MAX:-2.28}"

smoke_test_backend_bundle() {
  local backend_bin="$1"
  local log_file="$2"
  local port="${CHIPVERIFY_REDHAT_BACKEND_SMOKE_PORT:-7362}"

  rm -f "${log_file}"
  if ! "${backend_bin}" --self-test-packaged >"${log_file}" 2>&1; then
    echo "Red Hat backend packaged self-test failed (graphify + verification imports). Log: ${log_file}" >&2
    tail -n 120 "${log_file}" >&2 || true
    exit 1
  fi
  CHIPVERIFY_SECRET_KEY="packaging-smoke-secret" \
  CHIPVERIFY_BACKEND_HOST=127.0.0.1 \
  CHIPVERIFY_BACKEND_PORT="${port}" \
  CHIPVERIFY_REQUIRE_ACTIVATION=false \
  CHIPVERIFY_CLOUD_CONTROL_DISABLED=true \
  CHIPVERIFY_LLM_API_KEY_REQUIRED=false \
  "${backend_bin}" >>"${log_file}" 2>&1 &
  local backend_pid=$!

  for _ in $(seq 1 45); do
    if curl --silent --fail "http://127.0.0.1:${port}/api/v1/health" >/dev/null 2>&1; then
      kill "${backend_pid}" 2>/dev/null || true
      wait "${backend_pid}" 2>/dev/null || true
      echo "Red Hat backend smoke test passed on port ${port}."
      return 0
    fi
    if ! kill -0 "${backend_pid}" 2>/dev/null; then
      break
    fi
    sleep 1
  done

  kill "${backend_pid}" 2>/dev/null || true
  wait "${backend_pid}" 2>/dev/null || true
  echo "Red Hat backend smoke test failed. Log: ${log_file}" >&2
  tail -n 120 "${log_file}" >&2 || true
  exit 1
}

cleanup_stage() {
  rm -rf "${STAGE_ROOT}"
  mkdir -p "${STAGE_ROOT}"
  touch "${STAGE_ROOT}/.gitkeep"
}
trap cleanup_stage EXIT

echo "[1/7] Preparing Red Hat Python packaging environment"
mkdir -p "${REPO_ROOT}/.packaging"
if [[ -x "${VENV_PYTHON}" ]]; then
  VENV_VERSION="$("${VENV_PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  if [[ "${VENV_VERSION}" != "${PYTHON_VERSION_MAJOR_MINOR}" ]]; then
    echo "Existing Red Hat packaging venv uses Python ${VENV_VERSION:-unknown}; recreating for Python ${PYTHON_VERSION_MAJOR_MINOR}."
    rm -rf "${VENV_ROOT}"
  fi
fi
if [[ ! -x "${VENV_PYTHON}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_ROOT}"
fi

if [[ "${CHIPVERIFY_SKIP_REDHAT_PIP_INSTALL:-false}" == "true" ]]; then
  "${VENV_PYTHON}" -c 'import PyInstaller' \
    || { echo "CHIPVERIFY_SKIP_REDHAT_PIP_INSTALL=true but PyInstaller is missing." >&2; exit 1; }
  echo "Skipping Red Hat pip install; using existing packaging venv."
else
  "${VENV_PYTHON}" -m pip install --upgrade pip
  grep -Ev '^[[:space:]]*-e[[:space:]]+' \
    "${REPO_ROOT}/backend/requirements.txt" > "${PACKAGING_REQUIREMENTS}"
  "${VENV_PYTHON}" -m pip install -r "${PACKAGING_REQUIREMENTS}" pyinstaller
fi

echo "[2/7] Building Red Hat-compatible backend"
if [[ "${CHIPVERIFY_SKIP_REDHAT_BACKEND_BUILD:-false}" == "true" ]]; then
  echo "Skipping Red Hat backend build; using existing backend bundle."
else
  rm -rf "${BACKEND_BUILD_ROOT}"
  CHIPVERIFY_PYTHON_BIN="${VENV_PYTHON}" \
    bash "${REPO_ROOT}/backend/runtime/linux/build_backend.sh" \
    "${REPO_ROOT}/backend" \
    "${BACKEND_BUILD_ROOT}"
fi

BACKEND_DIST="${BACKEND_BUILD_ROOT}/dist/chipverify-backend"
BACKEND_BIN="${BACKEND_DIST}/chipverify-backend"
if [[ ! -x "${BACKEND_BIN}" ]]; then
  echo "Red Hat backend bundle was not produced." >&2
  exit 1
fi
file "${BACKEND_BIN}" || true

echo "[2a/7] Verifying Red Hat GLIBC compatibility"
bash "${GLIBC_COMPAT_SCRIPT}" "${BACKEND_DIST}" "${REDHAT_GLIBC_MAX}"

echo "[2b/7] Smoke testing Red Hat-compatible backend"
smoke_test_backend_bundle \
  "${BACKEND_BIN}" \
  "${BACKEND_BUILD_ROOT}/backend-smoke.log"

echo "[3/7] Staging Electron runtime resources"
rm -rf "${STAGE_ROOT}"
mkdir -p "${STAGE_BACKEND}" "${STAGE_BIN}"
cp -a "${BACKEND_DIST}/." "${STAGE_BACKEND}/"

BACKEND_LAUNCH_WRAPPER="${REPO_ROOT}/backend/runtime/linux/launch-backend.sh"
if [[ -f "${BACKEND_LAUNCH_WRAPPER}" ]]; then
  cp -f "${BACKEND_LAUNCH_WRAPPER}" "${STAGE_BACKEND}/launch-backend.sh"
  chmod +x "${STAGE_BACKEND}/launch-backend.sh"
  echo "Staged backend launch wrapper: ${STAGE_BACKEND}/launch-backend.sh"
fi

SECRET_KEY="$("${VENV_PYTHON}" -c 'import secrets; print(secrets.token_hex(32))')"
export CHIPVERIFY_SECRET_KEY="${SECRET_KEY}"
STAGED_ENV="${STAGE_BACKEND}/.env"
SOURCE_ENV="${REPO_ROOT}/backend/.env"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/stage_embedded_runtime_env.sh"
export PYTHON_BIN="${VENV_PYTHON}"
export CHIPVERIFY_LLM_PROVIDER="${CHIPVERIFY_LLM_PROVIDER:-bedrock}"
export MODEL_PROVIDER="${MODEL_PROVIDER:-${CHIPVERIFY_LLM_PROVIDER}}"
export CHIPVERIFY_REQUIRE_PACKAGED_LLM_KEY="${CHIPVERIFY_REQUIRE_PACKAGED_LLM_KEY:-true}"
stage_embedded_runtime_env "${SOURCE_ENV}"

if [[ -f "${REPO_ROOT}/backend/license.key" ]]; then
  cp "${REPO_ROOT}/backend/license.key" "${STAGE_BACKEND}/license.key"
fi

resolve_tool() {
  local env_value="$1"
  local command_name="$2"
  if [[ -n "${env_value}" && -x "${env_value}" ]]; then
    printf '%s' "${env_value}"
    return 0
  fi
  command -v "${command_name}" 2>/dev/null || true
}

EDA_TOOLS_BIN="${REPO_ROOT}/backend/runtime/linux/eda-tools/bin"
if [[ ! -x "${EDA_TOOLS_BIN}/slang" || ! -x "${EDA_TOOLS_BIN}/svls" ]]; then
  echo "[3a/7] Fetching Slang + svls for TruthCore and RTL lint"
  bash "${SCRIPT_DIR}/fetch_linux_eda_tools.sh"
fi

if [[ -z "${CHIPVERIFY_SLANG_BIN:-}" && -x "${EDA_TOOLS_BIN}/slang" ]]; then
  export CHIPVERIFY_SLANG_BIN="${EDA_TOOLS_BIN}/slang"
fi
if [[ -z "${CHIPVERIFY_SVLS_BIN:-}" && -x "${EDA_TOOLS_BIN}/svls" ]]; then
  export CHIPVERIFY_SVLS_BIN="${EDA_TOOLS_BIN}/svls"
fi

SLANG_SOURCE="$(resolve_tool "${CHIPVERIFY_SLANG_BIN:-}" slang)"
SVLS_SOURCE="$(resolve_tool "${CHIPVERIFY_SVLS_BIN:-}" svls)"

if [[ -n "${SLANG_SOURCE}" ]]; then
  cp "${SLANG_SOURCE}" "${STAGE_BIN}/slang"
  chmod +x "${STAGE_BIN}/slang"
  echo "Bundled Slang: ${SLANG_SOURCE}"
  "${STAGE_BIN}/slang" --version | head -1 || true
else
  echo "WARNING: Slang was not found; TruthCore will use the configured degraded fallback." >&2
fi

if [[ -n "${SVLS_SOURCE}" ]]; then
  cp "${SVLS_SOURCE}" "${STAGE_BIN}/svls"
  chmod +x "${STAGE_BIN}/svls"
  echo "Bundled svls: ${SVLS_SOURCE}"
  "${STAGE_BIN}/svls" --version | head -1 || true
else
  echo "WARNING: svls was not found; live SystemVerilog lint will be unavailable." >&2
fi

cat > "${STAGE_ROOT}/runtime-manifest.json" <<EOF
{
  "product": "chipverify-desktop",
  "platform": "linux",
  "linux_flavor": "redhat",
  "builder": "${PRETTY_NAME:-${ID}}",
  "builder_glibc": "${GLIBC_VERSION}",
  "target_glibc_max": "${REDHAT_GLIBC_MAX}",
  "backend_runtime": "runtime/backend/chipverify-backend",
  "slang_runtime": "runtime/bin/slang",
  "slang_bundled": $([[ -n "${SLANG_SOURCE}" ]] && echo true || echo false),
  "svls_runtime": "runtime/bin/svls",
  "svls_bundled": $([[ -n "${SVLS_SOURCE}" ]] && echo true || echo false),
  "activation_required": true,
  "target": "${TARGET}"
}
EOF

echo "[4/7] Ensuring Node dependencies"
if [[ ! -d "${REPO_ROOT}/node_modules" ]]; then
  npm ci
fi
if [[ ! -d "${REPO_ROOT}/frontend/node_modules" ]]; then
  npm --prefix "${REPO_ROOT}/frontend" ci
fi

echo "[5/7] Building frontend"
npm run build:frontend

echo "[6/7] Building Red Hat Linux desktop package"
if [[ "${TARGET}" == "all" ]]; then
  npx electron-builder --linux AppImage rpm --x64 --publish never \
    --config.linux.artifactName='${productName}-${version}-redhat-${arch}.${ext}'
else
  npx electron-builder --linux "${TARGET}" --x64 --publish never \
    --config.linux.artifactName='${productName}-${version}-redhat-${arch}.${ext}'
fi

UNPACKED_BACKEND="${REPO_ROOT}/dist-electron/linux-unpacked/resources/runtime/backend/chipverify-backend"
if [[ -f "${UNPACKED_BACKEND}" ]]; then
  chmod +x "${UNPACKED_BACKEND}"
  echo "Running packaged self-test on embedded Red Hat backend..."
  "${UNPACKED_BACKEND}" --self-test-packaged
  echo "EMBEDDED_BACKEND_SELF_TEST=ok"
fi

echo "[7/7] Collecting Red Hat artifacts"
rm -rf "${DIST_REDhat}"
mkdir -p "${DIST_REDhat}"
find "${REPO_ROOT}/dist-electron" -maxdepth 1 -type f \
  \( -name '*redhat*.AppImage' -o -name '*redhat*.AppImage.blockmap' -o -name '*redhat*.rpm' -o -name 'latest-linux.yml' \) \
  -exec cp -f {} "${DIST_REDhat}/" \;

mapfile -t ARTIFACTS < <(
  find "${DIST_REDhat}" -maxdepth 1 -type f \
    \( -name '*redhat*.AppImage' -o -name '*redhat*.AppImage.blockmap' -o -name '*redhat*.rpm' -o -name 'latest-linux.yml' \) \
    -print | sort
)
if [[ "${#ARTIFACTS[@]}" -eq 0 ]]; then
  echo "No Red Hat Linux installer artifact was produced." >&2
  exit 1
fi

for artifact in "${ARTIFACTS[@]}"; do
  echo "REDHAT_LINUX_ARTIFACT=${artifact}"
done
