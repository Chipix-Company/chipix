#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${CHIPVERIFY_PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
  elif command -v python3.10 >/dev/null 2>&1; then
    PYTHON_BIN="python3.10"
  else
    PYTHON_BIN="python3"
  fi
fi

VENV_ROOT="${REPO_ROOT}/.packaging/pyinstaller-redhat-venv"
VENV_PYTHON="${VENV_ROOT}/bin/python"
PACKAGING_REQUIREMENTS="${REPO_ROOT}/.packaging/requirements-redhat.txt"
BACKEND_BUILD_ROOT="${REPO_ROOT}/backend/runtime/redhat/build"
BACKEND_DIST="${BACKEND_BUILD_ROOT}/dist/chipverify-backend"
BACKEND_BIN="${BACKEND_DIST}/chipverify-backend"
REDHAT_GLIBC_MAX="${CHIPVERIFY_REDHAT_GLIBC_MAX:-2.28}"
SMOKE_SCRIPT="${REPO_ROOT}/scripts/packaging/smoke_test_backend_bundle.sh"
GLIBC_COMPAT_SCRIPT="${REPO_ROOT}/scripts/packaging/check_linux_glibc_compat.sh"

if [[ ! -f /etc/os-release ]]; then
  echo "This script must be run inside a Linux builder." >&2
  exit 2
fi

source /etc/os-release
if [[ "${ID_LIKE:-} ${ID:-}" != *"rhel"* && "${ID_LIKE:-} ${ID:-}" != *"centos"* && "${ID_LIKE:-} ${ID:-}" != *"fedora"* ]]; then
  echo "Red Hat backend must be built on a RHEL-family distro; current ID=${ID:-unknown} ID_LIKE=${ID_LIKE:-unknown}." >&2
  exit 2
fi

PYTHON_VERSION_MAJOR_MINOR="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "${PYTHON_VERSION_MAJOR_MINOR}" in
  3.10|3.11|3.12|3.13|3.14) ;;
  *)
    echo "Python 3.10+ is required; found ${PYTHON_VERSION_MAJOR_MINOR} from ${PYTHON_BIN}." >&2
    exit 1
    ;;
esac

cd "${REPO_ROOT}"
mkdir -p "${REPO_ROOT}/.packaging"

echo "[1/5] Preparing Red Hat Python packaging environment"
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

echo "[2/5] Building Red Hat-compatible backend"
rm -rf "${BACKEND_BUILD_ROOT}"
CHIPVERIFY_PYTHON_BIN="${VENV_PYTHON}" \
  bash "${REPO_ROOT}/backend/runtime/linux/build_backend.sh" \
  "${REPO_ROOT}/backend" \
  "${BACKEND_BUILD_ROOT}"

if [[ ! -x "${BACKEND_BIN}" ]]; then
  echo "Red Hat backend bundle was not produced." >&2
  exit 1
fi

echo "[3/5] Verifying Red Hat GLIBC compatibility"
bash "${GLIBC_COMPAT_SCRIPT}" "${BACKEND_DIST}" "${REDHAT_GLIBC_MAX}"

echo "[4/5] Smoke testing Red Hat-compatible backend"
bash "${SMOKE_SCRIPT}" \
  "${BACKEND_BIN}" \
  "${CHIPVERIFY_REDHAT_BACKEND_SMOKE_PORT:-7362}" \
  "${BACKEND_BUILD_ROOT}/backend-smoke.log"

echo "[5/5] Backend bundle ready"
echo "REDHAT_BACKEND_DIST=${BACKEND_DIST}"
