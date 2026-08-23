#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARTIFACT="${1:-}"
BACKEND_BIN="${CHIPVERIFY_LINUX_SMOKE_BACKEND_BIN:-${REPO_ROOT}/backend/runtime/linux/build/dist/chipverify-backend/chipverify-backend}"
BACKEND_PORT="${CHIPVERIFY_LINUX_SMOKE_BACKEND_PORT:-7350}"
DESKTOP_PORT="${CHIPVERIFY_LINUX_SMOKE_DESKTOP_PORT:-7351}"
LOG_DIR="${REPO_ROOT}/.packaging/linux-smoke"

mkdir -p "${LOG_DIR}"

if [[ ! -x "${BACKEND_BIN}" ]]; then
  echo "Native Linux backend is missing: ${BACKEND_BIN}" >&2
  exit 1
fi

wait_for_health() {
  local port="$1"
  local attempts="${2:-90}"
  local index
  for ((index = 0; index < attempts; index += 1)); do
    if curl --silent --fail "http://127.0.0.1:${port}/api/v1/health" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

echo "[1/3] Testing native PyInstaller backend"
CHIPVERIFY_SECRET_KEY=linux-smoke-test \
CHIPVERIFY_REQUIRE_ACTIVATION=false \
CHIPVERIFY_BACKEND_PORT="${BACKEND_PORT}" \
"${BACKEND_BIN}" >"${LOG_DIR}/backend.log" 2>&1 &
BACKEND_PID=$!
trap 'kill "${BACKEND_PID:-}" "${DESKTOP_PID:-}" 2>/dev/null || true' EXIT

if ! wait_for_health "${BACKEND_PORT}" 60; then
  echo "Native backend did not become healthy. Log: ${LOG_DIR}/backend.log" >&2
  exit 1
fi
curl --silent --fail "http://127.0.0.1:${BACKEND_PORT}/api/v1/health"
kill "${BACKEND_PID}" 2>/dev/null || true
wait "${BACKEND_PID}" 2>/dev/null || true
unset BACKEND_PID

if [[ -z "${ARTIFACT}" ]]; then
  ARTIFACT="$(find "${REPO_ROOT}/dist-electron" -maxdepth 1 -type f -name '*linux*.AppImage' -print -quit)"
fi
if [[ -z "${ARTIFACT}" || ! -f "${ARTIFACT}" ]]; then
  echo "Linux AppImage not found. Pass it as the first argument." >&2
  exit 1
fi

echo "[2/3] Inspecting AppImage"
chmod +x "${ARTIFACT}"
APPIMAGE_EXTRACT_AND_RUN=1 "${ARTIFACT}" --appimage-help >/dev/null 2>&1 || true

if ! command -v xvfb-run >/dev/null 2>&1; then
  echo "xvfb-run is required for the Electron smoke test." >&2
  exit 1
fi

echo "[3/3] Launching packaged Electron app and waiting for embedded backend"
CHIPVERIFY_REQUIRE_ACTIVATION=false \
CHIPVERIFY_BACKEND_PORT="${DESKTOP_PORT}" \
CHIPVERIFY_BACKEND_URL="http://127.0.0.1:${DESKTOP_PORT}" \
APPIMAGE_EXTRACT_AND_RUN=1 \
xvfb-run -a "${ARTIFACT}" --no-sandbox >"${LOG_DIR}/desktop.log" 2>&1 &
DESKTOP_PID=$!

if ! wait_for_health "${DESKTOP_PORT}" 90; then
  echo "Packaged desktop backend did not become healthy. Log: ${LOG_DIR}/desktop.log" >&2
  exit 1
fi

curl --silent --fail "http://127.0.0.1:${DESKTOP_PORT}/api/v1/health"
echo
echo "LINUX_SMOKE_TEST=passed"
