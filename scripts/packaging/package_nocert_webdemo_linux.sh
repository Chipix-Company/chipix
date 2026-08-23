#!/usr/bin/env bash
# Browser-only Linux bundle: PyInstaller backend serves frontend/dist at /app.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

VERSION="$(node -p "require('./package.json').version")"
OUTPUT_ROOT="${OUTPUT_ROOT:-dist-shareable/linux-no-cert}"
BUNDLE_NAME="ChipVerify-NoCert-WebDemo-${VERSION}-linux"
BUNDLE_DIR="${OUTPUT_ROOT}/${BUNDLE_NAME}"

echo "[1/6] Building browser frontend..."
npm run build:frontend

echo "[2/6] Preparing packaging venv..."
VENV_ROOT="${REPO_ROOT}/.packaging/pyinstaller-venv-linux"
VENV_PYTHON="${VENV_ROOT}/bin/python"
if [[ ! -x "${VENV_PYTHON}" ]]; then
  python3 -m venv "${VENV_ROOT}"
fi
"${VENV_PYTHON}" -m pip install --upgrade pip
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/install_backend_pyinstaller_deps.sh"
install_backend_pyinstaller_deps "${VENV_PYTHON}" "${REPO_ROOT}/backend/requirements.txt"

echo "[3/6] Building backend ELF..."
export PYTHON_BIN="${VENV_PYTHON}"
bash "${REPO_ROOT}/backend/runtime/linux/build_backend_elf.sh"

echo "[4/6] Assembling no-cert bundle..."
rm -rf "${BUNDLE_DIR}"
mkdir -p "${BUNDLE_DIR}/backend" "${BUNDLE_DIR}/frontend/dist" "${BUNDLE_DIR}/runtime/backend"

cp -a "${REPO_ROOT}/backend/runtime/linux/build/dist/chipverify-backend/." "${BUNDLE_DIR}/runtime/backend/"
cp -a "${REPO_ROOT}/frontend/dist/." "${BUNDLE_DIR}/frontend/dist/"

STAGED_ENV="${BUNDLE_DIR}/runtime/backend/.env"
SOURCE_ENV="${REPO_ROOT}/backend/.env"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/stage_embedded_runtime_env.sh"
export PYTHON_BIN="${VENV_PYTHON}"
stage_embedded_runtime_env "${SOURCE_ENV}"
set_env_value CHIPVERIFY_FRONTEND_DIST "${BUNDLE_DIR}/frontend/dist"

cat > "${BUNDLE_DIR}/launch-chipverify.sh" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="${ROOT}/runtime/backend/chipverify-backend"
FRONTEND_DIST="${ROOT}/frontend/dist"
ENV_FILE="${ROOT}/runtime/backend/.env"
LOG_DIR="${ROOT}/logs"
mkdir -p "${LOG_DIR}"

export CHIPVERIFY_FRONTEND_DIST="${FRONTEND_DIST}"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

"${BACKEND}" >"${LOG_DIR}/backend.log" 2>&1 &
BACKEND_PID=$!

cleanup() {
  if kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

HOST="${CHIPVERIFY_BACKEND_HOST:-127.0.0.1}"
PORT="${CHIPVERIFY_BACKEND_PORT:-7348}"
HEALTH_URL="http://${HOST}:${PORT}/api/v1/health"

for _ in $(seq 1 60); do
  if curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

URL="http://${HOST}:${PORT}/app/"
echo "ChipVerify is ready at ${URL}"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${URL}" >/dev/null 2>&1 || true
elif command -v gnome-open >/dev/null 2>&1; then
  gnome-open "${URL}" >/dev/null 2>&1 || true
fi

echo "Press Ctrl+C to stop."
wait "${BACKEND_PID}"
LAUNCHER
chmod +x "${BUNDLE_DIR}/launch-chipverify.sh"
chmod +x "${BUNDLE_DIR}/runtime/backend/chipverify-backend"

echo "[5/6] Creating archive..."
mkdir -p "${OUTPUT_ROOT}"
TARBALL="${OUTPUT_ROOT}/${BUNDLE_NAME}.tar.gz"
tar -czf "${TARBALL}" -C "${OUTPUT_ROOT}" "${BUNDLE_NAME}"

echo "[6/6] Done"
echo "BUNDLE_DIR=${BUNDLE_DIR}"
echo "TARBALL=${TARBALL}"
echo "Run: tar -xzf ${TARBALL} && cd ${BUNDLE_NAME} && ./launch-chipverify.sh"
