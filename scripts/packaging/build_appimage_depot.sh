#!/usr/bin/env bash
# Build the embedded Linux AppImage on Depot's fast remote builders (with persistent
# cache) and export it to ./dist-electron — no local container engine required.
#
# Prerequisites (one-time):
#   1. Install the Depot CLI:   npm install -g @depot/cli   (or see https://depot.dev/docs/cli/installation)
#   2. Authenticate:            depot login
#   3. First run prompts you to pick/create a Depot project and saves depot.json.
#
# Then just run:
#   bash scripts/packaging/build_appimage_depot.sh
#   npm run package:appimage:depot
#   powershell -File scripts/packaging/build_appimage_depot.ps1   (Windows)
#
# The build fails early if the frozen backend is missing services.verification.* modules
# (--self-test-packaged in build_backend_elf.sh + package_embedded_linux.sh).
#
# Optional env vars (passed as Docker build-args):
#   CHIPVERIFY_DEMO_GEMINI_API_KEY
#   CHIPVERIFY_DEMO_OPENAI_API_KEY
#   CHIPVERIFY_SENTRY_DSN
#   CHIPVERIFY_SENTRY_ENVIRONMENT   (default: production)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

OUTPUT_DIR="${REPO_ROOT}/dist-electron"

if ! command -v depot >/dev/null 2>&1; then
  echo "Depot CLI not found. Install it with:  npm install -g @depot/cli" >&2
  echo "Then run 'depot login' before retrying." >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

# depot build accepts the same flags as docker buildx build (see https://depot.dev/docs/container-builds/overview).
# --target artifact + type=local export writes the AppImage straight to the host.
echo "Building Linux AppImage on Depot (target: artifact) -> ${OUTPUT_DIR}"
depot build \
  -f Dockerfile.appimage \
  --platform linux/amd64 \
  --target artifact \
  --build-arg "CHIPVERIFY_DEMO_GEMINI_API_KEY=${CHIPVERIFY_DEMO_GEMINI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_OPENAI_API_KEY=${CHIPVERIFY_DEMO_OPENAI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_SENTRY_DSN=${CHIPVERIFY_SENTRY_DSN:-}" \
  --build-arg "CHIPVERIFY_SENTRY_ENVIRONMENT=${CHIPVERIFY_SENTRY_ENVIRONMENT:-production}" \
  -o "type=local,dest=dist-electron" \
  .

echo
echo "Done. Artifacts in ${OUTPUT_DIR}:"
find "${OUTPUT_DIR}" -maxdepth 1 \( -name '*.AppImage' -o -name 'latest-linux.yml' -o -name '*.blockmap' \) -type f -print || true

APPIMAGE_FILE="$(find "${OUTPUT_DIR}" -maxdepth 1 -name '*.AppImage' -type f | head -1 || true)"
if [[ -n "${APPIMAGE_FILE}" ]]; then
  echo
  echo "Run on Linux:  chmod +x $(basename "${APPIMAGE_FILE}") && ./$(basename "${APPIMAGE_FILE}")"
fi
