#!/usr/bin/env bash
set -euo pipefail

BACKEND_BIN="${1:-}"
BACKEND_PORT="${2:-7350}"
LOG_FILE="${3:-/tmp/chipverify-backend-smoke.log}"
ATTEMPTS="${CHIPVERIFY_BACKEND_SMOKE_ATTEMPTS:-45}"

if [[ -z "${BACKEND_BIN}" || ! -x "${BACKEND_BIN}" ]]; then
  echo "Backend executable is missing or not executable: ${BACKEND_BIN}" >&2
  exit 2
fi

rm -f "${LOG_FILE}"
if ! "${BACKEND_BIN}" --self-test-graphify >"${LOG_FILE}" 2>&1; then
  echo "Backend Graphify self-test failed. Log: ${LOG_FILE}" >&2
  tail -n 120 "${LOG_FILE}" >&2 || true
  exit 1
fi

CHIPVERIFY_SECRET_KEY="${CHIPVERIFY_SECRET_KEY:-packaging-smoke-secret}" \
CHIPVERIFY_BACKEND_HOST=127.0.0.1 \
CHIPVERIFY_BACKEND_PORT="${BACKEND_PORT}" \
CHIPVERIFY_REQUIRE_ACTIVATION=false \
CHIPVERIFY_CLOUD_CONTROL_DISABLED=true \
CHIPVERIFY_LLM_API_KEY_REQUIRED=false \
"${BACKEND_BIN}" >>"${LOG_FILE}" 2>&1 &
BACKEND_PID=$!
trap 'kill "${BACKEND_PID:-}" 2>/dev/null || true' EXIT

for _ in $(seq 1 "${ATTEMPTS}"); do
  if curl --silent --fail "http://127.0.0.1:${BACKEND_PORT}/api/v1/health"; then
    echo
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
    trap - EXIT
    echo "Backend smoke test passed on port ${BACKEND_PORT}."
    exit 0
  fi

  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    break
  fi

  sleep 1
done

kill "${BACKEND_PID}" 2>/dev/null || true
wait "${BACKEND_PID}" 2>/dev/null || true
trap - EXIT

echo "Backend smoke test failed. Log: ${LOG_FILE}" >&2
tail -n 120 "${LOG_FILE}" >&2 || true
exit 1
