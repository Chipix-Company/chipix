#!/usr/bin/env bash
# Build the embedded Linux AppImage inside a Debian bookworm container, then copy
# artifacts back to ./dist-electron on the host.
#
# Run from ANY machine with Docker (Windows + Docker Desktop, macOS, or Linux).
#
#   bash scripts/packaging/build_appimage_docker.sh
#   npm run package:appimage:docker
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

IMAGE_TAG="chipix-appimage:latest"
CONTAINER_NAME="chipix-appimage-build"
OUTPUT_DIR="${REPO_ROOT}/dist-electron"

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
echo "Using container engine: ${ENGINE}"

echo "[1/3] Building AppImage packaging image (${IMAGE_TAG})..."
"${ENGINE}" build \
  -f "${REPO_ROOT}/Dockerfile.appimage" \
  --build-arg "CHIPVERIFY_DEMO_GEMINI_API_KEY=${CHIPVERIFY_DEMO_GEMINI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_OPENAI_API_KEY=${CHIPVERIFY_DEMO_OPENAI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_SENTRY_DSN=${CHIPVERIFY_SENTRY_DSN:-}" \
  --build-arg "CHIPVERIFY_SENTRY_ENVIRONMENT=${CHIPVERIFY_SENTRY_ENVIRONMENT:-production}" \
  -t "${IMAGE_TAG}" \
  "${REPO_ROOT}"

echo "[2/3] Running the AppImage build inside the container..."
"${ENGINE}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
"${ENGINE}" run --name "${CONTAINER_NAME}" "${IMAGE_TAG}"

echo "[3/3] Copying artifacts to ${OUTPUT_DIR}..."
mkdir -p "${OUTPUT_DIR}"
TMP_COPY="$(mktemp -d)"
"${ENGINE}" cp "${CONTAINER_NAME}:/src/dist-electron/." "${TMP_COPY}/" || {
  echo "Failed to copy build output from container." >&2
  "${ENGINE}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  exit 1
}
find "${TMP_COPY}" -maxdepth 1 -type f \( -name '*.AppImage' -o -name 'latest-linux.yml' -o -name '*.blockmap' \) \
  -exec cp -f {} "${OUTPUT_DIR}/" \;
rm -rf "${TMP_COPY}"
"${ENGINE}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo
echo "Done. Artifacts in ${OUTPUT_DIR}:"
find "${OUTPUT_DIR}" -maxdepth 1 \( -name '*.AppImage' -o -name 'latest-linux.yml' -o -name '*.blockmap' \) -type f -print || true
