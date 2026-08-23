#!/usr/bin/env bash
# Extract a Red Hat AppImage and verify bundled slang, svls, and backend self-test.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APPIMAGE="${1:-${REPO_ROOT}/dist-redhat/ChipVerify_Desktop-0.2.1-redhat-x86_64.AppImage}"
EXTRACT="${CHIPVERIFY_APPIMAGE_EXTRACT_DIR:-/tmp/chipverify-appimage-test}"

if [[ ! -f "${APPIMAGE}" ]]; then
  echo "AppImage not found: ${APPIMAGE}" >&2
  exit 1
fi

rm -rf "${EXTRACT}"
mkdir -p "${EXTRACT}"
cd "${EXTRACT}"
chmod +x "${APPIMAGE}"
"${APPIMAGE}" --appimage-extract >/dev/null

MANIFEST="$(find squashfs-root -name runtime-manifest.json | head -1)"
SLANG="$(find squashfs-root -path '*/runtime/bin/slang' -type f | head -1)"
SVLS="$(find squashfs-root -path '*/runtime/bin/svls' -type f | head -1)"
BACKEND="$(find squashfs-root -path '*/runtime/backend/chipverify-backend' -type f | head -1)"

echo "=== runtime-manifest.json ==="
if [[ -n "${MANIFEST}" ]]; then
  cat "${MANIFEST}"
else
  echo "MISSING"
  exit 1
fi

echo
echo "=== Bundled tools ==="
echo "slang: ${SLANG:-MISSING}"
echo "svls: ${SVLS:-MISSING}"
echo "backend: ${BACKEND:-MISSING}"

fail=0
if [[ -z "${SLANG}" ]]; then fail=1; else chmod +x "${SLANG}" && "${SLANG}" --version | head -1; fi
if [[ -z "${SVLS}" ]]; then fail=1; else chmod +x "${SVLS}" && "${SVLS}" --version | head -1; fi
if [[ -z "${BACKEND}" ]]; then fail=1; else
  chmod +x "${BACKEND}"
  echo
  echo "=== Backend packaged self-test ==="
  "${BACKEND}" --self-test-packaged
  echo "BACKEND_SELF_TEST=ok"
fi

if [[ "${fail}" -ne 0 ]]; then
  echo "APPIMAGE_BUNDLE_TEST=failed" >&2
  exit 1
fi

echo "APPIMAGE_BUNDLE_TEST=ok"
