#!/usr/bin/env bash
# Build a RHEL-compatible AppImage inside AlmaLinux 8 via Docker BuildKit (no Depot required).
#
#   bash scripts/packaging/build_redhat_appimage_docker.sh
#   npm run package:redhat:appimage:docker
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

OUTPUT_DIR="${REPO_ROOT}/dist-redhat"
mkdir -p "${OUTPUT_DIR}"

ENGINE="${CHIPVERIFY_CONTAINER_ENGINE:-}"
if [[ -z "${ENGINE}" ]]; then
  if command -v docker >/dev/null 2>&1; then
    ENGINE="docker"
  elif command -v podman >/dev/null 2>&1; then
    ENGINE="podman"
  else
    echo "Neither docker nor podman was found on PATH." >&2
    exit 1
  fi
fi

echo "Building Red Hat AppImage (${ENGINE}, AlmaLinux 8) -> ${OUTPUT_DIR}"
"${ENGINE}" build \
  -f "${REPO_ROOT}/Dockerfile.redhat.appimage" \
  --platform linux/amd64 \
  --target artifact \
  --build-arg "CHIPVERIFY_DEMO_BEDROCK_API_KEY=${CHIPVERIFY_DEMO_BEDROCK_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_GEMINI_API_KEY=${CHIPVERIFY_DEMO_GEMINI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_OPENAI_API_KEY=${CHIPVERIFY_DEMO_OPENAI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_SENTRY_DSN=${CHIPVERIFY_SENTRY_DSN:-}" \
  --build-arg "CHIPVERIFY_SENTRY_ENVIRONMENT=${CHIPVERIFY_SENTRY_ENVIRONMENT:-production}" \
  -o "type=local,dest=${OUTPUT_DIR}" \
  "${REPO_ROOT}"

echo
echo "Done. Artifacts in ${OUTPUT_DIR}:"
find "${OUTPUT_DIR}" -maxdepth 1 -type f \
  \( -name '*redhat*.AppImage' -o -name '*.blockmap' -o -name 'latest-linux.yml' \) \
  -print || true
