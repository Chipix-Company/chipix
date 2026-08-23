#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET_ROOT="${1:-/root/chipverify-redhat-build}"
ARCHIVE="${TMPDIR:-/tmp}/chipverify-redhat-src.tar"

if [[ "${SOURCE_ROOT}" != /mnt/* ]]; then
  echo "Source root is ${SOURCE_ROOT}; this helper is intended to copy from a mounted workspace into native WSL storage." >&2
fi

echo "Preparing native Red Hat build root: ${TARGET_ROOT}"
rm -rf "${TARGET_ROOT}"
mkdir -p "${TARGET_ROOT}"
rm -f "${ARCHIVE}"

tar -C "${SOURCE_ROOT}" \
  --exclude='./backend/runtime/linux/build' \
  --exclude='./backend/runtime/redhat/build' \
  --exclude='./backend/__pycache__' \
  --exclude='./scripts/__pycache__' \
  -cf "${ARCHIVE}" \
  backend scripts package.json package-lock.json README.md AGENTS.md

tar -C "${TARGET_ROOT}" -xf "${ARCHIVE}"
rm -f "${ARCHIVE}"

echo "REDHAT_BUILD_ROOT=${TARGET_ROOT}"
