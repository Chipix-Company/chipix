#!/usr/bin/env bash
set -euo pipefail
echo 0 > /proc/sys/fs/binfmt_misc/WSLInterop 2>/dev/null || true
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

BACKEND="/root/chipverify-build/dist-electron/linux-unpacked/resources/runtime/backend/chipverify-backend"

echo "=== Binary check ==="
ls -lh "${BACKEND}"
file "${BACKEND}"

echo ""
echo "=== GLIBC check ==="
objdump -p "${BACKEND}" 2>/dev/null | grep GLIBC | sort -t. -k2 -n | tail -5 || \
  strings "${BACKEND}" | grep GLIBC_ | sort -u | tail -10 || true

echo ""
echo "=== Starting backend (10s timeout) ==="
CHIPVERIFY_SECRET_KEY=smoketest \
CHIPVERIFY_REQUIRE_ACTIVATION=false \
CHIPVERIFY_BACKEND_PORT=7348 \
CHIPVERIFY_CLOUD_CONTROL_DISABLED=true \
CHIPVERIFY_LLM_API_KEY_REQUIRED=false \
"${BACKEND}" > /tmp/smoke.log 2>&1 &
PID=$!
echo "Backend PID=${PID}"

for i in $(seq 1 15); do
  sleep 1
  if curl -sf http://127.0.0.1:7348/api/v1/health > /dev/null 2>&1; then
    HEALTH=$(curl -sf http://127.0.0.1:7348/api/v1/health)
    echo ""
    echo "Health response: ${HEALTH}"
    echo ""
    echo "SMOKE_TEST=PASSED ✅"
    kill "${PID}" 2>/dev/null || true
    exit 0
  fi
  if ! kill -0 "${PID}" 2>/dev/null; then
    echo "Backend process died after ${i}s"
    break
  fi
  echo "  Waiting... ${i}s"
done

echo "SMOKE_TEST=FAILED ❌"
echo "--- Backend log ---"
cat /tmp/smoke.log || echo "(empty)"
kill "${PID}" 2>/dev/null || true
exit 1
