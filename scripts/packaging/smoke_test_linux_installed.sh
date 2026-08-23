#!/usr/bin/env bash
set -euo pipefail

DESKTOP_BIN="${1:-/usr/bin/chipverify-desktop}"
BACKEND_PORT="${CHIPVERIFY_LINUX_INSTALLED_SMOKE_PORT:-7352}"
LOG_FILE="${CHIPVERIFY_LINUX_INSTALLED_SMOKE_LOG:-/tmp/chipverify-installed-smoke.log}"
HEALTH_FILE="${CHIPVERIFY_LINUX_INSTALLED_HEALTH_FILE:-/tmp/chipverify-installed-health.json}"

if [[ ! -x "${DESKTOP_BIN}" ]]; then
  echo "Installed ChipVerify launcher is missing: ${DESKTOP_BIN}" >&2
  exit 1
fi

if ! command -v xvfb-run >/dev/null 2>&1; then
  echo "xvfb-run is required for the installed desktop smoke test." >&2
  exit 1
fi

rm -f "${LOG_FILE}" "${HEALTH_FILE}"

CHIPVERIFY_REQUIRE_ACTIVATION=false \
CHIPVERIFY_BACKEND_PORT="${BACKEND_PORT}" \
CHIPVERIFY_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}" \
xvfb-run -a "${DESKTOP_BIN}" --no-sandbox >"${LOG_FILE}" 2>&1 &
DESKTOP_PID=$!

cleanup() {
  kill "${DESKTOP_PID:-}" 2>/dev/null || true
  wait "${DESKTOP_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 90); do
  if curl --silent --fail \
    "http://127.0.0.1:${BACKEND_PORT}/api/v1/health" >"${HEALTH_FILE}"; then
    echo "DEB_HEALTH=$(cat "${HEALTH_FILE}")"
    echo "DEB_SMOKE_TEST=passed"
    exit 0
  fi
  sleep 1
done

echo "Installed desktop backend did not become healthy. Log: ${LOG_FILE}" >&2
tail -n 80 "${LOG_FILE}" >&2 || true
exit 1
