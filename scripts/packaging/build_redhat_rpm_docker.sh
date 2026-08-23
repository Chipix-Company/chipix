#!/usr/bin/env bash
# Build a RHEL-native .rpm for ChipVerify Desktop inside an AlmaLinux 8 (glibc 2.28)
# container, then copy the artifacts back to ./dist-electron on the host.
#
# Run this from ANY machine with Docker (Windows + Docker Desktop, macOS, or Linux).
# The resulting RPM installs and runs on RHEL 8/9, Rocky, and AlmaLinux.
#
#   bash scripts/packaging/build_redhat_rpm_docker.sh
#
# Optional demo API keys:
#   CHIPVERIFY_DEMO_BEDROCK_API_KEY=... bash scripts/packaging/build_redhat_rpm_docker.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

IMAGE_TAG="chipix-redhat-rpm:latest"
CONTAINER_NAME="chipix-redhat-rpm-build"
OUTPUT_DIR="${REPO_ROOT}/dist-electron"

# Use whichever OCI engine is available (Docker or Podman — both are CLI-compatible).
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

echo "[1/3] Building AlmaLinux 8 packaging image (${IMAGE_TAG})..."
"${ENGINE}" build \
  -f "${REPO_ROOT}/Dockerfile.redhat" \
  --build-arg "CHIPVERIFY_DEMO_BEDROCK_API_KEY=${CHIPVERIFY_DEMO_BEDROCK_API_KEY:-${BEDROCK_API_KEY:-}}" \
  --build-arg "CHIPVERIFY_LLM_PROVIDER=${CHIPVERIFY_LLM_PROVIDER:-bedrock}" \
  --build-arg "CHIPVERIFY_DEMO_GEMINI_API_KEY=${CHIPVERIFY_DEMO_GEMINI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_OPENAI_API_KEY=${CHIPVERIFY_DEMO_OPENAI_API_KEY:-}" \
  -t "${IMAGE_TAG}" \
  "${REPO_ROOT}"

echo "[2/3] Running the RPM build inside the container..."
"${ENGINE}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
"${ENGINE}" run --name "${CONTAINER_NAME}" "${IMAGE_TAG}"

echo "[3/3] Copying artifacts to ${OUTPUT_DIR}..."
mkdir -p "${OUTPUT_DIR}"
# Copy out only the distributable installers, not the whole unpacked tree.
TMP_COPY="$(mktemp -d)"
"${ENGINE}" cp "${CONTAINER_NAME}:/src/dist-electron/." "${TMP_COPY}/" || {
  echo "Failed to copy build output from container." >&2
  "${ENGINE}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  exit 1
}
find "${TMP_COPY}" -maxdepth 1 -type f \( -name '*.rpm' -o -name '*.AppImage' -o -name '*.yml' -o -name '*.blockmap' \) \
  -exec cp -f {} "${OUTPUT_DIR}/" \;
rm -rf "${TMP_COPY}"
"${ENGINE}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo
echo "Done. Artifacts in ${OUTPUT_DIR}:"
find "${OUTPUT_DIR}" -maxdepth 1 -name '*.rpm' -type f -print || true
echo
echo "Install on Red Hat with:  sudo dnf install ./$(cd "${OUTPUT_DIR}" && ls -1 *.rpm 2>/dev/null | head -1)"
