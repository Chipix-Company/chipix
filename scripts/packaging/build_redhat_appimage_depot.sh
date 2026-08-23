#!/usr/bin/env bash
# Build a RHEL-compatible AppImage (glibc 2.28 backend + Bedrock defaults) on Depot and
# export to ./dist-redhat — no local container engine required.
#
# Prerequisites:
#   npm install -g @depot/cli && depot login
#
# Usage:
#   bash scripts/packaging/build_redhat_appimage_depot.sh
#   npm run package:redhat:appimage:depot
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

OUTPUT_DIR="${REPO_ROOT}/dist-redhat"

if ! command -v depot >/dev/null 2>&1; then
  echo "Depot CLI not found. Install: npm install -g @depot/cli && depot login" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

echo "Building Red Hat AppImage on Depot (AlmaLinux 8, Bedrock) -> ${OUTPUT_DIR}"
depot build \
  -f Dockerfile.redhat.appimage \
  --platform linux/amd64 \
  --target artifact \
  --build-arg "CHIPVERIFY_DEMO_BEDROCK_API_KEY=${CHIPVERIFY_DEMO_BEDROCK_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_GEMINI_API_KEY=${CHIPVERIFY_DEMO_GEMINI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_OPENAI_API_KEY=${CHIPVERIFY_DEMO_OPENAI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_SENTRY_DSN=${CHIPVERIFY_SENTRY_DSN:-}" \
  --build-arg "CHIPVERIFY_SENTRY_ENVIRONMENT=${CHIPVERIFY_SENTRY_ENVIRONMENT:-production}" \
  -o "type=local,dest=dist-redhat" \
  .

echo
echo "Done. Artifacts in ${OUTPUT_DIR}:"
find "${OUTPUT_DIR}" -maxdepth 1 -type f \( -name '*redhat*.AppImage' -o -name 'latest-linux.yml' -o -name '*.blockmap' \) -print || true

APPIMAGE="$(find "${OUTPUT_DIR}" -maxdepth 1 -name '*redhat*.AppImage' -type f | head -1 || true)"
if [[ -n "${APPIMAGE}" ]]; then
  echo
  echo "Run on Red Hat / AlmaLinux / Rocky:"
  echo "  chmod +x $(basename "${APPIMAGE}")"
  echo "  ./$(basename "${APPIMAGE}") --appimage-extract-and-run"
  VERSION="$(node -p "require('${REPO_ROOT}/package.json').version" 2>/dev/null || echo "")"
  echo
  echo "Publish to Convex (metadata only — binaries stay on GitHub Releases):"
  echo "  ./scripts/convex/set-release-version.sh --version ${VERSION} --platform linux --github-tag v${VERSION}"
fi
